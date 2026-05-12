import uuid
import json
import httpx
import os
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy.orm import Session

from app.services.extractor import extract_text
from app.services.vector_store import get_qdrant, get_sparse_encoder, search_vdb, QDRANT_COLLECTION, VECTOR_NAME_DENSE, VECTOR_NAME_SPARSE
from app.core.logic import (
    clean_text, chunk_document, extract_paper_metadata, embed_chunks,
    estimate_cost, generate_paper_summary,
    extract_structured_fields, extract_bibliography,
    generate_bibtex_key, build_bibtex_entry,
)
from app.core.database import User, UsageLog, PaperSummary, PaperDetails, PaperReference, get_db
from app.core.config import EMBEDDING_MODEL
from app.core.globals import openai_client
from app.core.cache import invalidate_exact_cache_for_tenant, invalidate_semantic_cache_for_tenant
from qdrant_client.http import models as rest

async def process_ingestion(
    file_content: bytes, 
    filename: str, 
    user: User, 
    db: Session
) -> Dict[str, Any]:
    """Legacy wrapper for tool compatibility."""
    async for event in stream_process_ingestion(file_content, filename, user, db):
        if event.get("type") == "completed":
            return event["result"]
    return {}

async def stream_process_ingestion(
    file_content: bytes, 
    filename: str, 
    user: User, 
    db: Session
):
    """
    Streaming ingestion logic for real-time progress updates.
    Yields: {"type": "progress", "value": int, "message": str}
    """
    tenant_id = user.tenant_id
    
    # Stage 1: Extraction
    yield {"type": "progress", "value": 10, "message": f"Extracting text from {filename}..."}
    from io import BytesIO
    class MockFile:
        def __init__(self, content, name):
            self._content = content
            self.filename = name
        async def read(self):
            return self._content
    
    mock_file = MockFile(file_content, filename)
    raw_text, method, image_map = await extract_text(mock_file)
    
    # Stage 2: Semantic Metadata
    yield {"type": "progress", "value": 25, "message": "Analyzing paper metadata..."}
    paper_meta = await extract_paper_metadata(openai_client, raw_text)
    
    # Stage 3: Cleaning
    yield {"type": "progress", "value": 35, "message": "Cleaning and normalizing text..."}
    cleaned_text, clean_meta = clean_text(raw_text)
    
    # Stage 4: Unique doc_id
    doc_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{tenant_id}_{filename}"))
    
    # Stage 5: Chunking & Linking
    yield {"type": "progress", "value": 45, "message": "Chunking document and linking visuals..."}
    chunks, chunk_stats = chunk_document(cleaned_text, doc_id, tenant_id, image_map)
    ingested_at = datetime.utcnow().isoformat()
    for c in chunks:
        c.metadata["filename"] = filename
        c.metadata["ingested_at"] = ingested_at
        c.metadata.update(paper_meta.model_dump())
        
    # Stage 6: Dense Embedding
    yield {"type": "progress", "value": 60, "message": f"Generating dense embeddings for {len(chunks)} chunks..."}
    embedded = await embed_chunks(openai_client, chunks)
    
    # Stage 7: Sparse Encoding
    yield {"type": "progress", "value": 80, "message": "Generating sparse vectors (BM25)..."}
    s_encoder = get_sparse_encoder()
    s_vectors = list(s_encoder.embed([c.text for c in embedded]))
    
    # Stage 8: Vector Persistence
    yield {"type": "progress", "value": 88, "message": "Indexing into Qdrant vault..."}
    qdrant = get_qdrant()
    points = []
    for c, s_vec in zip(embedded, s_vectors):
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, c.chunk_id))
        points.append(rest.PointStruct(
            id=point_id,
            vector={
                VECTOR_NAME_DENSE: c.embedding,
                VECTOR_NAME_SPARSE: rest.SparseVector(
                    indices=s_vec.indices.tolist(),
                    values=s_vec.values.tolist()
                )
            },
            payload=c.to_dict()
        ))

    qdrant.upsert(collection_name=QDRANT_COLLECTION, points=points)

    # Any new ingestion can change retrieval answers, so invalidate the fast exact
    # cache for this tenant before we report completion back to the client.
    await invalidate_exact_cache_for_tenant(tenant_id)
    await invalidate_semantic_cache_for_tenant(tenant_id)

    # Stage 9: Auto-summary + paper_details upsert
    yield {"type": "progress", "value": 92, "message": "Generating paper summary..."}
    summary_text = await generate_paper_summary(openai_client, raw_text, paper_meta.title or filename)
    if summary_text:
        db.query(PaperSummary).filter(
            PaperSummary.tenant_id == tenant_id,
            PaperSummary.filename == filename
        ).delete()
        db.add(PaperSummary(tenant_id=tenant_id, filename=filename, summary=summary_text))
        db.flush()

    # Stage 9a: Structured extraction (contribution / dataset / baselines / limitations)
    # Runs as a separate LLM call so a failure here never breaks the main ingest.
    yield {"type": "progress", "value": 94, "message": "Extracting structured research fields..."}
    structured = {}
    refs_data = []
    try:
        structured = await extract_structured_fields(openai_client, raw_text)
        refs_data  = await extract_bibliography(openai_client, raw_text)
    except Exception as e:
        pass

    # Upsert paper_details row (canonical per-paper record used by BibTeX, graph, etc.)
    import json as _json
    bibtex_key = generate_bibtex_key(
        paper_meta.authors, paper_meta.year, paper_meta.title or filename
    )
    # Ensure key is unique within tenant
    existing_keys = {
        row.bibtex_key
        for row in db.query(PaperDetails.bibtex_key)
            .filter(PaperDetails.tenant_id == tenant_id)
            .all()
    }
    base_key = bibtex_key
    suffix = ord('a')
    while bibtex_key in existing_keys:
        bibtex_key = f"{base_key}{chr(suffix)}"
        suffix += 1

    db.query(PaperDetails).filter(
        PaperDetails.tenant_id == tenant_id,
        PaperDetails.filename == filename
    ).delete()
    db.add(PaperDetails(
        tenant_id=tenant_id,
        filename=filename,
        title=paper_meta.title,
        authors=_json.dumps(paper_meta.authors),
        year=paper_meta.year,
        doi=paper_meta.doi,
        journal=paper_meta.journal,
        keywords=_json.dumps(paper_meta.keywords),
        bibtex_key=bibtex_key,
        contribution=structured.get("contribution"),
        dataset=structured.get("dataset"),
        baselines=_json.dumps(structured.get("baselines") or []),
        limitations=structured.get("limitations"),
    ))

    # Stage 9b: Save extracted bibliography references
    if refs_data:
        db.query(PaperReference).filter(
            PaperReference.tenant_id == tenant_id,
            PaperReference.source_filename == filename
        ).delete()
        for ref in refs_data:
            db.add(PaperReference(
                tenant_id=tenant_id,
                source_filename=filename,
                ref_title=ref.get("title", ""),
                ref_authors=_json.dumps(ref.get("authors") or []),
                ref_year=ref.get("year"),
                ref_doi=ref.get("doi"),
                arxiv_id=ref.get("arxiv_id"),
            ))
    db.flush()

    # Stage 10: Usage Logging
    total_tokens = sum(c.token_count for c in embedded)
    cost = estimate_cost(total_tokens, 0, EMBEDDING_MODEL)
    
    usage = UsageLog(
        tenant_id=tenant_id, user_id=user.id,
        event_type="ingest", model_name=EMBEDDING_MODEL,
        tokens_input=total_tokens, estimated_cost_usd=cost
    )
    db.add(usage)
    db.commit()
    
    result = {
        "filename": filename,
        "status": "success",
        "tenant_id": tenant_id,
        "metadata": paper_meta.model_dump() if hasattr(paper_meta, "model_dump") else paper_meta,
        "cleaning": clean_meta.model_dump() if hasattr(clean_meta, "model_dump") else clean_meta,
        "chunking": chunk_stats.model_dump() if hasattr(chunk_stats, "model_dump") else chunk_stats
    }
    yield {"type": "progress", "value": 100, "message": "Indexing complete."}
    yield {"type": "completed", "result": result}

async def download_and_ingest_arxiv(
    arxiv_id: str, 
    user: User, 
    db: Session
) -> str:
    """Downloads a PDF from Arxiv and ingests it."""
    url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    async with httpx.AsyncClient() as client:
        response = await client.get(url, follow_redirects=True)
        if response.status_code != 200:
            return f"Failed to download paper from Arxiv (Status: {response.status_code})"
        
        file_content = response.content
        filename = f"arxiv_{arxiv_id}.pdf"
        
        try:
            result = await process_ingestion(file_content, filename, user, db)
            return f"Successfully ingested paper '{arxiv_id}': {result['metadata'].title}"
        except Exception as e:
            return f"Error during ingestion of {arxiv_id}: {str(e)}"
