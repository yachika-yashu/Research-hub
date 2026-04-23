# 01 Request Lifecycle

## Request chosen

This traces a single chat query from Streamlit to final response:

- user enters prompt in `dashboard.py`
- frontend calls `POST /api/v1/query`
- backend checks Redis exact cache, then Qdrant semantic cache
- backend invokes LangGraph
- graph may call vault retrieval
- response streams back over SSE
- backend logs usage and trace data

## Step-by-step runtime path

### 1. UI event creation

In `dashboard.py`, `st.chat_input()` captures the prompt. The script appends the user message into `st.session_state.messages`, then starts `handle_stream()`.

Why this matters:

- Streamlit reruns the script on interaction, so persistent UI state must live in `st.session_state`.
- The frontend keeps `thread_id` client-side so future requests can resume the same LangGraph checkpoint thread.

### 2. Network call from UI to API

`stream_query_backend()` builds JSON:

```json
{
  "query": "...",
  "filters": {"year_min": 1990, "year_max": 2026},
  "thread_id": "...",
  "tenant_id": "..."
}
```

Then it opens an SSE connection with `aconnect_sse()` to `/api/v1/query`.

Network boundary:

- Browser-like Streamlit client to FastAPI over HTTP
- long-lived streaming response instead of short request-response

Why SSE is used:

- the model emits tokens incrementally
- tool usage events can be surfaced before the final answer
- long-running retrieval does not force the frontend to poll

### 3. FastAPI request parsing

`handle_query()` in `app/api/routes.py` accepts:

- `query_req: QueryRequest`
- `request: Request`
- `background_tasks: BackgroundTasks`
- `current_user: User = Depends(get_current_user)`

What happens internally:

- FastAPI reads JSON body
- Pydantic validates it into `QueryRequest`
- dependency injection runs JWT auth
- a SQLAlchemy session is created and used by auth dependency

At this point, the system has:

- authenticated username
- authoritative `tenant_id` from the user record
- optional `thread_id`

Important design note:

The request model carries `tenant_id`, but the handler trusts `current_user.tenant_id`, not the caller-provided value. That is the correct security boundary.

### 4. Thread identity and graph config

`handle_query()` computes:

- `thread_id = query_req.thread_id or str(uuid.uuid4())`
- `config = {"configurable": {"thread_id": thread_id}}`

This config is passed into LangGraph. The checkpointer uses it as the key for persisted state.

Why this exists:

- without `thread_id`, every query is stateless
- with `thread_id`, the graph can restore prior `messages`
- chat history endpoint can later read the same checkpoint

### 5. Exact cache lookup in Redis

Before any embedding work, FastAPI calls:

```python
exact_cached_res = await exact_cache_get(current_user.tenant_id, query_req.query)
```

Internal path:

1. SHA-256 hashes the raw query into a compact key suffix.
2. Redis key is built as `researhub:query:{tenant_id}:{query_hash}`.
3. FastAPI performs `GET`.
4. On hit, JSON is returned directly and the graph is skipped.

Why this exists:

- avoids embedding cost for identical repeated prompts
- gives a much cheaper hot-path than semantic cache
- supports TTL-based expiration

### 6. Semantic cache lookup

Before graph execution, FastAPI calls:

```python
cached_res = await semantic_cache_get(current_user.tenant_id, query_req.query)
```

Internal path:

1. OpenAI embeddings API converts the query into a dense vector.
2. Qdrant searches `hanuman_semantic_cache`.
3. Qdrant filter restricts by `tenant_id`.
4. If top score is at least `0.95`, cached JSON is returned.

Why this decision was made:

- semantic cache hits can bypass expensive LLM calls
- tenant filter prevents cross-team leakage
- using embedding similarity avoids exact-string cache misses

Failure behavior:

- cache errors are swallowed and treated as optimization bypass
- the request still continues

### 7. Initial SSE event

The backend immediately yields:

```json
{"status":"agent_started","thread_id":"..."}
```

Why:

- frontend needs the authoritative `thread_id`
- user gets immediate UI feedback before the LLM returns anything

### 8. Graph invocation

On cache miss, the backend builds inputs:

```python
inputs = {
    "messages": [("user", query_req.query)],
    "tenant_id": current_user.tenant_id
}
```

Then it runs:

```python
async for event in graph.astream_events(inputs, config, version="v1"):
```

Internal LangGraph behavior:

1. checkpointed state is loaded using `thread_id`
2. new user message is merged into state
3. `agent` node runs `call_model`
4. `call_model` prepends a system message if none exists
5. `ChatOpenAI.bind_tools()` enables tool-call capable completion
6. model either emits answer text or tool calls
7. if tool calls exist, graph transitions to `tools`
8. `ToolNode` executes the selected tool
9. graph loops back to `agent`
10. when no more tool calls are requested, graph ends

### 9. Tool execution path

The most important tool is `rag_tool`.

If invoked, it:

1. inspects query for visual terms
2. optionally expands depth and query terms
3. builds `QueryFilters`
4. calls `search_vdb()`

`search_vdb()` then:

1. builds tenant-scoped Qdrant filter
2. creates dense embedding with OpenAI
3. creates sparse BM25 vector with `SparseTextEmbedding`
4. executes hybrid fusion query using RRF
5. falls back to dense-only search if hybrid search returns nothing
6. reranks results with cross-encoder if enabled
7. returns payloads, not raw Qdrant point objects

Data transformation sequence:

- natural language query
- dense vector
- sparse vector
- fused result set
- reranked payload list
- formatted context string injected back into graph

### 10. Streaming back to UI

FastAPI listens to LangGraph events and translates them into UI-friendly SSE frames:

- `on_chat_model_stream` -> `{"type":"token","content":"..."}`
- `on_tool_start` -> `{"type":"tool_start","tool":"..."}`
- `on_tool_end` -> `{"type":"tool_end","tool":"...","output":"..."}`

The frontend reads each event and:

- appends tokens to the visible answer
- updates status box when tools start/end
- stores final answer in session state

Why this translation layer exists:

- raw LangGraph events are too framework-specific for the UI
- SSE payloads become a stable frontend contract

### 11. Metrics emission

After graph completion, FastAPI computes:

- latency in milliseconds
- input token count via `tiktoken`
- output token count via `tiktoken`

Then it emits a `metrics` SSE event.

Why compute after the stream:

- latency depends on full lifecycle, not just first token
- output token count requires the completed response buffer

### 12. Background governance

FastAPI does not write governance data inline during streaming. It schedules:

```python
background_tasks.add_task(finalize_query_governance, ...)
```

`finalize_query_governance()` then:

1. opens a fresh SQLAlchemy session
2. estimates token cost
3. writes `UsageLog`
4. writes `TraceLog`
5. commits the transaction
6. stores exact cache entry in Redis
7. stores semantic cache entry in Qdrant

Why background this work:

- response stream finishes faster
- user-perceived latency excludes persistence overhead
- the system tolerates the user disconnecting after answer delivery

Tradeoff:

- usage logging can fail after the answer was already shown
- audit data is therefore best-effort, not strongly coupled to response delivery

## Storage touches during one request

### Redis

- exact cache lookup
- exact cache write after completion
- exact-cache invalidation after tenant ingestion

### Qdrant

- semantic cache lookup
- possibly vector retrieval through `rag_tool`
- semantic cache write after completion

### SQLite `checkpoints.db`

- LangGraph loads and updates thread state keyed by `thread_id`

### Relational database

- auth dependency queries `users`
- background governance inserts `usage_logs`
- background governance inserts `trace_logs`
- locally this is SQLite
- in production this can be PostgreSQL via `DATABASE_URL`

## What does not happen

The current request path does **not** do any of the following:

- no queue broker handoff
- no separate HTML SPA call path

Those remain future-production improvements.
