# 12 Performance And Scaling

## Current bottlenecks

### 1. OpenAI calls

The system uses OpenAI for:

- query embeddings
- chunk embeddings
- metadata extraction
- answer generation
- optional verification utilities

This dominates latency and cost.

### 2. CPU-heavy ingestion inside API process

Docling, OCR, PDF image conversion, and sparse encoding happen in the API process. That means ingestion can contend with query handling.

### 3. Local SQLite

SQLite is fine for single-node moderate traffic, but it becomes a concurrency choke point under many writers or multiple replicas.

### 4. Local filesystem assets

Image crops are served from local disk. That becomes awkward under autoscaling because each node has different local files unless volumes are shared.

### 5. Cross-encoder reranking

`TextCrossEncoder` improves retrieval precision but adds CPU latency after initial retrieval.

## Caching strategy

Current cache:

- exact response cache in Redis
- semantic response cache in Qdrant

Strengths:

- exact hits avoid both embedding and graph cost
- catches paraphrased repeat questions
- tenant-scoped

Weaknesses:

- semantic lookups still pay embedding cost
- no explicit invalidation when corpus changes

Current production strategy:

- exact-match short-lived cache in Redis
- semantic cache for expensive high-value repeated research questions
- exact-cache invalidation after new document ingestion

Still recommended:

- semantic cache versioning or tenant-scoped invalidation after ingestion

## Async benefits

Async helps this system primarily in:

- many simultaneous open SSE streams
- overlapping outbound HTTP waits
- low-overhead streaming of partial results

Async does **not** magically accelerate:

- OCR
- PDF parsing
- reranker compute
- local blocking library calls

So async improves concurrency for I/O-bound phases, but CPU-heavy ingestion still needs process separation for real scale.

## Horizontal scaling

### What scales well

- FastAPI app workers behind a load balancer
- Qdrant as separate service
- frontend and API as separate containers

### What does not scale cleanly today

- local SQLite files
- local checkpoint database
- local image asset directory

To scale horizontally, move those to shared systems:

- PostgreSQL for relational and possibly checkpoint data
- object storage for images
- Redis for ephemeral shared state

## Vertical scaling

Today the system mostly benefits from vertical scale:

- more CPU for OCR and reranking
- more memory for model utilities and file processing
- faster disk for local DB and Qdrant path storage

That matches the current single-node storage assumptions.

## Practical optimization opportunities

1. Move ingestion to background workers.
2. Replace per-request heavy initialization with warm singletons where safe.
3. Keep exact-match cache in front of semantic cache.
4. Add cache invalidation/versioning tied to corpus updates.
5. Move governance logging to a durable async queue.
6. Replace SQLite with PostgreSQL for concurrent production traffic.

## Minimal performance practice for this project

This codebase now includes a just-enough performance toolkit:

- `perf/benchmark_api.py` for repeatable endpoint benchmarks
- `perf/profile_hotspots.py` for targeted profiling
- `docs/PERF_BASELINE.md` for storing results

That is enough to answer practical questions like:

- Did query latency improve after a cache change?
- Is first-event latency getting worse?
- Is `search_vdb()` spending most of its time in reranking or network I/O?

## Most likely scaling path

### Phase 1

- one API service
- one Streamlit service
- one Qdrant service
- SQLite on persistent volume

### Phase 2

- PostgreSQL for durable relational state
- Redis for cache and job coordination
- background worker for ingestion
- shared object storage for extracted images

### Phase 3

- multiple API replicas
- load balancer with sticky routing only if checkpoint store remains local
- otherwise shared checkpoint backend and no sticky requirement
