# 02 System Design Principles

## Separation of concerns

### Where it appears

- `dashboard.py` owns presentation and UI session state.
- `app/api/routes.py` owns HTTP contracts and streaming transport.
- `app/core/graph.py` owns orchestration logic.
- `app/services/ingestion.py`, `extractor.py`, and `vector_store.py` own domain workflows.
- `app/core/database.py`, `app/core/cache.py`, and `app/core/qdrant.py` own storage access.

### Why it matters

This keeps transport concerns separate from retrieval and persistence. SSE formatting logic is not mixed into vector search. Auth is not mixed into chunking logic.

### What breaks if ignored

- route handlers become untestable
- storage migrations require touching UI code
- orchestration bugs get entangled with HTTP behavior
- failures become harder to localize

## Modularity

### Where it appears

- tools are individually wrapped with `@tool`
- the graph is compiled from nodes and edges
- ingestion pipeline is decomposed into extract, clean, chunk, embed, encode, persist
- auth, logging, config, and globals are isolated modules

### Why it matters

Modularity lets the system swap implementation details. For example, Qdrant retrieval can change without changing the FastAPI contract, and `rag_tool` can evolve without rewriting the graph skeleton.

### What breaks if ignored

- changing one tool risks regressions across the full graph
- local optimizations become global rewrites
- observability becomes coarse and misleading

## Scalability

### Where it appears

Horizontal-friendly parts:

- FastAPI app is ASGI and can run multiple Gunicorn workers
- Qdrant is separated as its own service in compose
- frontend is decoupled over HTTP

Vertical-only parts:

- SQLite databases are local files
- image storage is local disk
- LangGraph checkpointer is local SQLite

### Why it matters

True horizontal scale requires shared state. The API layer can scale out more easily than the state layer.

### What breaks if ignored

- multiple API instances would not share `research.db`, `checkpoints.db`, or local assets by default
- thread continuity breaks when a later request lands on another node
- local-disk persistence becomes a hidden single-node dependency

## Fault tolerance

### Where it appears

- cache lookup failure is treated as non-fatal
- retrieval has dense-only fallback if hybrid search fails
- extraction falls back from Docling to OCR
- governance logging rolls back its DB transaction on exception

### Why it matters

These choices preserve partial service in degraded states. Search can still work if sparse retrieval fails. Ingestion can still work if structured extraction fails.

### What breaks if ignored

- one failing optimization takes down the full request
- document ingestion becomes all-or-nothing
- model-serving transient issues cause hard user-visible failures more often

## Caching strategy

### Where it appears

- semantic query-response cache in `app/core/cache.py`
- cache storage is Qdrant, not Redis
- cache keying uses embedding similarity plus `tenant_id`

### Why it matters

Semantic caching is a better match than exact-key caching for natural language queries. It reduces cost and latency when users ask the same thing with slightly different wording.

### What breaks if ignored

- repeated prompts always incur full LLM and retrieval cost
- perceived latency stays high under common repeated queries

### Important caveat

The system currently has **no TTL or eviction policy** for semantic cache entries. That means stale answers and unbounded growth are real risks.

## Stateless vs stateful services

### Where it appears

Stateless-ish:

- frontend requests are HTTP-based
- API handlers derive auth and tenant from each request

Stateful:

- Streamlit session state stores auth token, messages, citations, thread ID
- LangGraph persists thread checkpoints
- SQLite stores users and governance records
- Qdrant stores vault and semantic cache

### Why it matters

Understanding where state lives determines scaling and failure handling. This system is not stateless end to end.

### What breaks if ignored

- load balancing without sticky/shared state breaks chat continuity
- node restarts lose local-only assets and state
- production behavior becomes nondeterministic across replicas

## Idempotency

### Where it appears

Partial idempotency exists in ingestion:

- `doc_id` is deterministic from `tenant_id` and filename
- chunk `point_id` is deterministic from `chunk_id`
- semantic cache `point_id` is deterministic from `tenant_id` and query

### Why it matters

Deterministic IDs let repeated writes overwrite the same logical item rather than endlessly duplicate it.

### What breaks if ignored

- retries create duplicate vector entries
- cache balloons with duplicate semantic equivalents
- governance logs become hard to reconcile

### Current limitation

Ingestion idempotency is only partial because metadata like `ingested_at` changes and usage logs are still appended on every ingest.
