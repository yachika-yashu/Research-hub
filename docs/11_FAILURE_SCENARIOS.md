# 11 Failure Scenarios

## Redis down

### Current reality

Redis now backs the exact-match cache, but it is treated as an optimization dependency rather than a hard dependency.

### Operational impact

- exact-cache reads and writes fail open
- repeated identical prompts become slower
- semantic cache and graph execution still work

### Production lesson

Unused infrastructure is dangerous because it confuses incident response.

## Database failure

In the current system this means SQLite failure, not PostgreSQL failure.

### If `research.db` is unavailable

Breaks:

- login and registration
- JWT user lookup
- usage dashboard endpoints
- governance logging

User-visible behavior:

- auth-protected endpoints fail early
- answers may still stream if auth already passed and only post-response logging fails

### If `checkpoints.db` is unavailable

Breaks:

- thread persistence
- chat history retrieval
- checkpointed continuation across turns

Potential symptom:

- each prompt behaves like a new chat or graph errors on startup

## Qdrant failure

### If vault collection is unavailable

Breaks:

- ingestion persistence
- document retrieval via `rag_tool`
- stats endpoint for vault size

User-visible behavior:

- uploaded files appear to process but cannot be searched if persistence fails
- graph may still answer from base model, but without local vault grounding

### If semantic cache collection is unavailable

Breaks:

- cache lookup and write path

User-visible behavior:

- slower and more expensive requests
- core functionality can still work if the exception is handled as cache bypass

## LLM timeout or provider failure

Affected calls:

- query generation through `ChatOpenAI`
- embedding generation
- metadata extraction
- faithfulness verification

User-visible behavior:

- query stream may stall or terminate with incomplete answer
- ingestion may fail during embedding or metadata stage
- semantic cache lookup can fail because it depends on embeddings

Design observation:

OpenAI is on the critical path for both retrieval and generation, not just final answer generation.

## API crash during stream

### During query

Symptoms:

- SSE connection closes abruptly
- frontend sees partial answer only
- background logging may never run

Why this is painful:

- streamed output is user-visible before durability work completes
- crash timing affects how much state was persisted

## Extraction failure

### Docling failure

Current behavior:

- falls back to OCR

User-visible impact:

- slower ingestion
- lower structural fidelity
- missing image linkage or table structure

## OCR failure

If OCR and Docling both fail:

- ingestion fails entirely
- no chunks reach Qdrant

## Worker overload

Potential causes:

- too many concurrent long SSE requests
- CPU-heavy OCR/doc processing inside async handlers
- reranker model load or repeated heavy retrieval

Symptoms:

- increased first-token latency
- stalled streams
- request timeouts

## What robust production handling should add

- explicit retry and timeout policies around provider calls
- structured exception-to-SSE error translation
- circuit-breaking or fallback mode when LLM provider is degraded
- health checks for Qdrant and DB beyond simple HTTP liveness
- offloading heavy ingestion steps to worker jobs instead of API workers
