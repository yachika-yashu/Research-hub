from typing import List, Optional
from qdrant_client import QdrantClient
from qdrant_client.http import models as rest
from fastembed import SparseTextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder

from app.core.config import (
    QDRANT_COLLECTION, VECTOR_NAME_DENSE, VECTOR_NAME_SPARSE,
    RERANKER_MODEL, ENABLE_RERANKING, RERANK_TOP_K, EMBEDDING_DIMENSIONS
)
from app.schemas.models import QueryFilters
from app.core.globals import openai_client
from app.core.qdrant import get_qdrant_client

# Global instances for singleton-like access
_sparse_encoder: Optional[SparseTextEmbedding] = None
_text_reranker: Optional[TextCrossEncoder] = None

def get_qdrant() -> QdrantClient:
    return get_qdrant_client()

def get_sparse_encoder() -> SparseTextEmbedding:
    global _sparse_encoder
    if _sparse_encoder is None:
        print("QDRANT: Loading Sparse Encoder (BM25)...")
        _sparse_encoder = SparseTextEmbedding(model_name="Qdrant/bm25")
    return _sparse_encoder

def get_reranker() -> TextCrossEncoder:
    global _text_reranker
    if _text_reranker is None:
        print(f"RERANKER: Loading {RERANKER_MODEL}...")
        _text_reranker = TextCrossEncoder(model_name=RERANKER_MODEL)
    return _text_reranker

async def init_db():
    """Initializes high-performance storage and semantic indexes."""
    client = get_qdrant()
    collections = client.get_collections().collections
    
    exists = False
    is_hybrid = False
    for c in collections:
        if c.name == QDRANT_COLLECTION:
            exists = True
            info = client.get_collection(QDRANT_COLLECTION)
            is_hybrid = isinstance(info.config.params.vectors, dict) and VECTOR_NAME_DENSE in info.config.params.vectors
            break

    if exists and not is_hybrid:
        print(f"MIGRATION: Deleting legacy collection '{QDRANT_COLLECTION}'...")
        client.delete_collection(QDRANT_COLLECTION)
        exists = False

    if not exists:
        print(f"Creating Hybrid Qdrant collection: {QDRANT_COLLECTION}...")
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config={
                VECTOR_NAME_DENSE: rest.VectorParams(size=EMBEDDING_DIMENSIONS, distance=rest.Distance.COSINE),
            },
            sparse_vectors_config={
                VECTOR_NAME_SPARSE: rest.SparseVectorParams()
            }
        )
        
        # Metadata Indexes (Step 25)
        print("QDRANT: Creating Metadata Indexes...")
        client.create_payload_index(QDRANT_COLLECTION, "tenant_id", rest.PayloadSchemaType.KEYWORD)
        client.create_payload_index(QDRANT_COLLECTION, "metadata.year", rest.PayloadSchemaType.INTEGER)
        client.create_payload_index(QDRANT_COLLECTION, "metadata.authors", rest.PayloadSchemaType.KEYWORD)
        client.create_payload_index(QDRANT_COLLECTION, "metadata.journal", rest.PayloadSchemaType.KEYWORD)
        client.create_payload_index(QDRANT_COLLECTION, "chunk_type", rest.PayloadSchemaType.KEYWORD)
        client.create_payload_index(QDRANT_COLLECTION, "metadata.ingested_at", rest.PayloadSchemaType.KEYWORD)
        
        print(f"Qdrant collection '{QDRANT_COLLECTION}' initialized with Hybrid + Metadata support.")
    else:
        print(f"Qdrant collection '{QDRANT_COLLECTION}' already exists.")

async def search_vdb(
    query: str,
    tenant_id: str,
    limit: int = 5,
    filters: Optional[QueryFilters] = None
) -> List[dict]:
    """
    Hybrid retrieval: combines dense (OpenAI embeddings) + sparse (BM25) search
    via Qdrant's RRF (Reciprocal Rank Fusion) to get the best of both worlds —
    dense for semantic similarity, sparse for exact keyword matching.
    Falls back to dense-only if hybrid fails (e.g. sparse index is empty on a
    fresh collection), so retrieval always returns something useful.
    tenant_id is always the first filter so no tenant ever sees another's data.
    """
    # tenant_id is mandatory — it's the multi-tenancy isolation boundary
    filter_conditions = [rest.FieldCondition(key="tenant_id", match=rest.MatchValue(value=tenant_id))]
    
    if filters:
        # Years: only apply if they are not the extremely broad defaults (1990/2025)
        # We also allow points with null years to be included by default in these range searches
        if filters.year_min and filters.year_min > 1990:
            filter_conditions.append(rest.FieldCondition(
                key="metadata.year", 
                range=rest.Range(gte=filters.year_min)
            ))
        if filters.year_max and filters.year_max < 2025:
            filter_conditions.append(rest.FieldCondition(
                key="metadata.year", 
                range=rest.Range(lte=filters.year_max)
            ))
        
        if filters.authors:
            for author in filters.authors:
                filter_conditions.append(rest.FieldCondition(key="metadata.authors", match=rest.MatchValue(value=author)))
        if filters.journal:
            filter_conditions.append(rest.FieldCondition(key="metadata.journal", match=rest.MatchValue(value=filters.journal)))
        if filters.chunk_type and filters.chunk_type != "all":
            filter_conditions.append(rest.FieldCondition(key="chunk_type", match=rest.MatchValue(value=filters.chunk_type)))
        if filters.filename:
            filter_conditions.append(rest.FieldCondition(key="metadata.filename", match=rest.MatchValue(value=filters.filename)))

    qdrant_filter = rest.Filter(must=filter_conditions)
    print(f"QDRANT: Final Filter -> {qdrant_filter}")

    # 1. Dense Embedding
    from app.core.config import EMBEDDING_MODEL
    dense_resp = await openai_client.embeddings.create(input=[query], model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIMENSIONS)
    dense_vector = dense_resp.data[0].embedding
    
    # 2. Sparse Embedding
    s_encoder = get_sparse_encoder()
    sparse_vector = list(s_encoder.query_embed(query))[0]
    
    client = get_qdrant()
    retrieval_limit = RERANK_TOP_K if ENABLE_RERANKING else limit

    # 3. Hybrid Fusion Search (RRF)
    print(f"QDRANT: Executing Hybrid Fusion Query (RRF) for Tenant {tenant_id}...")
    try:
        results = client.query_points(
            collection_name=QDRANT_COLLECTION,
            prefetch=[
                rest.Prefetch(query=dense_vector, using=VECTOR_NAME_DENSE, filter=qdrant_filter, limit=retrieval_limit),
                rest.Prefetch(
                    query=rest.SparseVector(indices=sparse_vector.indices.tolist(), values=sparse_vector.values.tolist()),
                    using=VECTOR_NAME_SPARSE, filter=qdrant_filter, limit=retrieval_limit
                )
            ],
            query=rest.FusionQuery(fusion=rest.Fusion.RRF),
            limit=retrieval_limit,
            with_payload=True
        ).points
    except Exception as e:
        print(f"QDRANT: Hybrid Query Failed: {e}. Falling back...")
        results = []

    # Dense-only fallback: hybrid fusion fails on fresh collections where the
    # sparse (BM25) index has no documents yet, or when the Qdrant server
    # version doesn't fully support the prefetch/fusion API.
    # query_filter= (not filter=) is the correct param name for query_points()
    # in Qdrant client ≥1.12 — using filter= here caused AssertionError silently
    # swallowed by the rag_tool, making the LLM hallucinate "refer to the paper".
    if not results:
        print("QDRANT: Hybrid Fusion returned zero results. Retrying with Semantic Fallback (Dense-only)...")
        try:
            results = client.query_points(
                collection_name=QDRANT_COLLECTION,
                query=dense_vector,
                using=VECTOR_NAME_DENSE,
                query_filter=qdrant_filter,
                limit=limit,
                with_payload=True
            ).points
        except Exception as e:
            print(f"QDRANT: Dense-only fallback also failed: {e}")
            results = []

    if not results: 
        print("QDRANT: All retrieval attempts failed (Zero matches found).")
        return []

    print(f"QDRANT: Successfully retrieved {len(results)} chunks.")
    initial_hits = [r.payload for r in results]
    passages = [r.payload.get("text", "") for r in results]

    # 3. Precision Reranking (Step 18)
    if ENABLE_RERANKING:
        reranker = get_reranker()
        rerank_scores = list(reranker.rerank(query, passages))
        for idx, score in enumerate(rerank_scores):
            initial_hits[idx]["precision_score"] = round(float(score), 4)
        
        # Sort by precision
        initial_hits = sorted(initial_hits, key=lambda x: x.get("precision_score", 0), reverse=True)

    return initial_hits[:limit]

async def list_unique_papers(tenant_id: str) -> List[dict]:
    """Returns a list of unique papers in the user's vault, sorted by most recent."""
    client = get_qdrant()
    
    # Scroll through all points for this tenant to find unique filenames
    # Since we only need metadata, we disable vectors to save bandwidth
    results, _ = client.scroll(
        collection_name=QDRANT_COLLECTION,
        scroll_filter=rest.Filter(
            must=[rest.FieldCondition(key="tenant_id", match=rest.MatchValue(value=tenant_id))]
        ),
        limit=5000, # Increased for Phase 14 Vault Authority
        with_payload=True,
        with_vectors=False
    )
    
    unique_papers = {}
    for point in results:
        meta = point.payload.get("metadata", {})
        filename = meta.get("filename")
        if not filename:
            continue
            
        ingested_at = meta.get("ingested_at", "Legacy/Prior to Update")
        
        # Keep the most recent record for each filename
        if filename not in unique_papers or ingested_at > unique_papers[filename]["ingested_at"]:
            unique_papers[filename] = {
                "filename": filename,
                "title": meta.get("title", "Unknown"),
                "authors": meta.get("authors", []),
                "year": meta.get("year"),
                "doi": meta.get("doi"),
                "journal": meta.get("journal"),
                "keywords": meta.get("keywords", []),
                "ingested_at": ingested_at
            }
    
    # Convert to list and sort by ingested_at descending
    papers = sorted(unique_papers.values(), key=lambda x: x["ingested_at"], reverse=True)
    return papers
