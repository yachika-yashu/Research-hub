# 10 Data Flow And State

## Query data flow

### 1. JSON input

The frontend sends JSON matching `QueryRequest`.

Shape:

- `query`
- `filters`
- `thread_id`
- `tenant_id`

### 2. Pydantic object

FastAPI converts JSON into a `QueryRequest` instance.

Why this step matters:

- types become explicit Python attributes
- malformed payloads are rejected before orchestration

### 3. Security state overlay

The route does not trust caller tenant context. It overlays:

- authenticated `current_user`
- authoritative `current_user.tenant_id`

That is the first critical state correction in the request path.

### 4. LangGraph input state

FastAPI converts request data into graph state:

- `messages`: list containing one user tuple
- `tenant_id`: tenant-scoped state carried into tools

### 5. Exact cache state

Before embeddings or graph execution:

- query text is hashed
- Redis key is generated per tenant
- Redis may return the final answer JSON immediately

### 6. Retrieval transformations

If the graph calls `rag_tool`, the query moves through:

- raw text query
- dense embedding vector
- sparse BM25 vector
- filtered Qdrant points
- reranked payload objects
- formatted retrieval context string

### 7. Model output state

The model emits:

- text tokens
- or tool call directives

LangGraph wraps those as message objects and appends them to state.

### 8. SSE transport objects

FastAPI converts internal events into small JSON fragments:

- token events
- tool status events
- metrics event

The frontend never sees raw LangGraph internal classes.

### 9. Durable state after completion

After the response:

- SQLite `usage_logs` gets cost/token metadata
- SQLite `trace_logs` gets prompt and verification payloads
- Redis exact cache gets answer JSON with TTL
- Qdrant semantic cache gets response JSON keyed by query embedding
- SQLite checkpoint state already contains message history by thread ID

## Ingestion data flow

### 1. Binary upload

The frontend sends multipart form data with the PDF bytes.

### 2. Bytes to extracted markdown

`extract_text()`:

- reads bytes
- saves temp PDF
- runs Docling conversion
- exports markdown
- saves image crops to `assets/images`
- produces `image_map`

Fallback:

- if Docling output is too short or fails, OCR text is generated instead

### 3. Raw text to cleaned text

`clean_text()` applies:

- Unicode normalization
- control-char removal
- ligature repair
- de-hyphenation
- repeated-line filtering

### 4. Cleaned text to chunk objects

`chunk_document()` creates `DocumentChunk` dataclass objects carrying:

- chunk text
- boundaries
- token count
- chunk type
- optional `media_url`
- metadata map

### 5. Chunk objects to vectors

- dense vectors from OpenAI embeddings
- sparse vectors from `fastembed`

### 6. Vectors to Qdrant points

Each chunk becomes `rest.PointStruct` with:

- deterministic ID
- dense vector
- sparse vector
- payload from `DocumentChunk.to_dict()`

### 7. Metadata to relational log

The ingest operation writes one `UsageLog` row summarizing embedding cost.

## State ownership map

### Frontend state

- auth token
- current messages shown in UI
- selected thread ID

### API in-memory state

- compiled graph on `app.state.graph`
- active checkpointer on `app.state.checkpointer`

### Filesystem state

- extracted images under `assets/images`
- SQLite files
- Qdrant local storage directory

### Vector state

- document chunks
- semantic cache embeddings

### Relational state

- users
- usage logs
- trace logs

## Why this matters for a rebuild

To rebuild from scratch, decide early which state is:

- ephemeral optimization state
- durable business state
- conversational memory
- retrieval corpus

This project currently spreads those categories across Streamlit session state, SQLite, Qdrant, and local files.
