import uuid
import json
import httpx
import os
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy.orm import Session

from app.services.extractor import extract_text
from app.services.vector_store import get_qdrant, get_sparse_encoder, search_vdb, QDRANT_COLLECTION, VECTOR_NAME_DENSE, VECTOR_NAME_SPARSE
from app.core.logic import clean_text, chunk_document, extract_paper_metadata, embed_chunks, estimate_cost
from app.core.database import User, UsageLog, get_db
from app.core.config import EMBEDDING_MODEL
from app.core.globals import openai_client
from app.core.cache import invalidate_exact_cache_for_tenant
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
    yield {"type": "progress", "value": 90, "message": "Indexing into Qdrant vault..."}
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
    
    # Stage 9: Usage Logging
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
