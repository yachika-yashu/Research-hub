# 09 Commands Line By Line

## `async def`

### What it does

Defines a coroutine function. Calling it returns a coroutine object that runs when awaited by the event loop.

### Why it is used

This system waits on:

- OpenAI API calls
- SSE streams
- async LangGraph operations
- async file reads

`async def` lets those waits yield control instead of blocking the whole worker.

### What breaks if removed

- `await` becomes illegal inside the function
- async libraries cannot be used correctly
- streaming endpoints would need a different synchronous implementation

## `await`

### What it does

Suspends the current coroutine until the awaited I/O completes, allowing the event loop to run other tasks.

### Why it is used

Examples in this codebase:

- `await semantic_cache_get(...)`
- `await llm_with_tools.ainvoke(messages)`
- `await openai_client.embeddings.create(...)`

### What breaks if removed

- you get coroutine objects instead of results
- I/O would not execute as intended
- type and runtime errors appear immediately

## `@app.post` and `@router.post`

### What it does

Registers a function as an HTTP POST handler for a specific path.

### Why it is used

- `/query` mutates or triggers server-side work
- `/ingest` uploads content and persists new data
- `/auth/token` accepts credentials

POST is correct because these operations are not safe cacheable reads.

### What breaks if removed

- FastAPI will not expose the endpoint
- clients get 404 or method mismatch

## `StreamingResponse(...)`

### What it does

Creates an HTTP response whose body is produced incrementally by an iterator or async generator.

### Why it is used

- query answers stream token by token
- ingestion progress streams stage by stage

### What breaks if removed

- the frontend would wait for full completion
- no progress bar
- no live token stream

## `BackgroundTasks.add_task(...)`

### What it does

Schedules work to run after the response is sent.

### Why it is used

Governance logging and cache updates happen after answer delivery so user-facing latency stays lower.

### What breaks if removed

- if simply deleted, logging and cache updates never happen
- if moved inline, streaming completes later and perceived latency rises

## `Depends(...)`

### What it does

Tells FastAPI to resolve a dependency before calling the route function.

### Why it is used

- auth is injected through `get_current_user`
- DB session lifecycle is injected through `get_db`

### What breaks if removed

- handlers must manually parse tokens and create DB sessions
- auth enforcement becomes inconsistent

## `create_engine(...)`

### What it does

Builds the SQLAlchemy database engine used for connections and ORM operations.

### Why it is used

The relational layer needs a single configured entry point for user and audit tables.

### What breaks if removed

- no database connectivity
- sessions cannot be created
- auth and governance tables become unavailable

## `db.query(...).filter(...).first()`

### What it does

Builds and executes a SELECT query through the ORM and returns the first row or `None`.

### Why it is used

Common patterns:

- lookup user by username during login
- lookup trace by usage ID

### What breaks if removed

- auth cannot resolve users
- trace and dashboard reads fail

## `db.add(...)`, `db.flush()`, `db.commit()`

### What they do

- `add` stages objects in the transaction
- `flush` pushes pending SQL so generated values are available
- `commit` finalizes the transaction

### Why they are used

`flush()` is used before writing `TraceLog` so the `UsageLog` primary key is available in the same transaction.

### What breaks if removed

- removing `add` means nothing is staged
- removing `flush` can leave dependent IDs unavailable before commit
- removing `commit` means durable data never reaches the DB

## `QdrantClient(...)`

### What it does

Creates the vector database client used for collection management, search, scroll, and upsert.

### Why it is used

Qdrant is the retrieval core and semantic cache store.

### What breaks if removed

- vault search disappears
- ingestion cannot persist vectors
- semantic cache cannot function

## `redis.set`

### What it does

Writes an exact-cache payload to Redis under a tenant-scoped key and applies TTL through the `ex=` argument.

### Why it is relevant

The query path now stores identical-prompt responses in Redis so repeated questions can bypass graph execution entirely.

### What breaks if removed

- exact identical prompts no longer get the low-latency cache hit path
- every repeat prompt falls through to semantic cache or full graph execution

## `cursor.execute`

### What it would do

Execute raw SQL against a DB cursor.

### Why it is relevant

The repository still uses SQLAlchemy ORM instead of raw DB cursors, even after the PostgreSQL upgrade path was added.

### What breaks if removed

Nothing directly, because the code still uses ORM session methods rather than raw cursor calls.

### Why SQLAlchemy is used instead

- ORM model mapping for `User`, `UsageLog`, and `TraceLog`
- session-scoped transaction control
- less handwritten SQL in route handlers

## `client.create_collection(...)`

### What it does

Creates a Qdrant collection with vector and sparse-vector configuration.

### Why it is used

The system needs a hybrid-search-capable collection before ingesting any chunks.

### What breaks if removed

- upsert/search fail because the collection does not exist

## `client.query_points(...)`

### What it does

Executes vector or hybrid search in Qdrant and returns matching points.

### Why it is used

- semantic cache lookup
- vault retrieval
- dense fallback retrieval

### What breaks if removed

- no retrieval path
- no cache lookup path

## `qdrant.upsert(...)`

### What it does

Inserts or overwrites points in a collection by ID.

### Why it is used

- persist document chunks during ingestion
- persist semantic cache responses

### What breaks if removed

- ingestion produces no searchable data
- cache writes are lost

## `chain.invoke`

### What it would do

Invoke a LangChain runnable synchronously.

### Why it is relevant

This project conceptually uses the runnable pattern, but the actual code uses `llm_with_tools.ainvoke(...)`, not `chain.invoke(...)`.

### What breaks if removed

Nothing directly, because it is not present in this codebase.

## `llm_with_tools.ainvoke(...)`

### What it does

Calls the bound chat model with current messages and awaits the model result.

### Why it is used

- tool-call capable model invocation
- async-friendly for network I/O
- clean fit with LangGraph node execution

### What breaks if removed

- the `agent` node cannot produce either tool calls or final answers
- the graph becomes inert

## `.run(...)`

### What it does

Legacy simple execution helper used here by:

- `arxiv_wrapper.run(query)`
- `python_repl.run(code)`

### Why it is used

Those utilities are string-in string-out helpers, so `.run()` is sufficient.

### What breaks if removed

- arXiv search tool and Python tool stop returning outputs

## `@tool`

### What it does

Wraps a Python function as a LangChain tool with schema metadata.

### Why it is used

It lets the LLM request tool execution in a structured way.

### What breaks if removed

- tool binding loses schema information
- the model cannot call those capabilities through the graph
