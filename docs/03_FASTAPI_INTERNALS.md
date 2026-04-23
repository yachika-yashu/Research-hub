# 03 FastAPI Internals

## Routing internals

`main.py` creates a `FastAPI` app and mounts two routers:

- `auth_router` at `/api/v1/auth`
- `api_router` at `/api/v1`

When a request arrives, FastAPI:

1. resolves the path and HTTP method
2. matches the correct route function
3. parses parameters from body, path, query, form, and dependencies
4. executes the handler in the ASGI event loop
5. serializes the return value or streams the response

Important routes:

- `@router.post("/query")`
- `@router.post("/ingest")`
- `@router.get("/chat/threads")`
- `@router.get("/chat/history/{thread_id}")`
- auth endpoints in `app/api/auth.py`

## Request parsing with Pydantic

FastAPI uses the type annotations on route functions to know how to parse input:

- `QueryRequest` for JSON body
- `UploadFile = File(...)` for multipart upload
- `OAuth2PasswordRequestForm = Depends()` for form-encoded login

Why Pydantic is used:

- request structure is declared once
- validation happens before business logic
- type conversions are consistent
- bad requests fail early with 422 errors

Example:

`QueryRequest` turns incoming JSON into a Python object with fields such as `query`, `thread_id`, `limit`, and `filters`.

## Async execution model

Most request handlers are `async def`. In ASGI, that means:

- the handler runs on an event loop
- `await` yields control while waiting on I/O
- one worker can interleave many connections if they spend time waiting

This matters here because the app does:

- SSE streaming
- outbound HTTP calls to OpenAI and arXiv
- async checkpoint I/O

Async helps most when the workload is I/O-bound.

## Dependency injection

FastAPI resolves shared dependencies via `Depends(...)`.

Examples:

- `current_user: User = Depends(get_current_user)`
- `db: Session = Depends(get_db)`

Execution order:

1. dependency graph is built
2. `get_db()` yields a SQLAlchemy session
3. `get_current_user()` consumes token and DB session
4. if auth succeeds, route handler runs
5. generator dependencies are finalized after request

Why this is useful:

- route code stays focused on business logic
- auth and DB lifecycle are reusable
- security is attached declaratively to endpoints

## Lifespan startup

`main.py` defines `lifespan(app)` with `@asynccontextmanager`.

Startup responsibilities:

- initialize Qdrant collections
- initialize semantic cache collection
- create relational tables
- create async LangGraph checkpointer
- compile graph and store it in `app.state`

Why this is done at startup instead of per request:

- model/tool graph compilation is shared
- collection creation should not race on every request
- checkpointer lifecycle needs app-level ownership

## ASGI vs WSGI

### WSGI

WSGI is synchronous and request-response oriented. A worker handles one request at a time unless threads or processes are layered on top.

### ASGI

ASGI supports async execution and protocols like HTTP streaming and WebSockets. FastAPI sits on Starlette and is designed for ASGI.

### Why ASGI matters here

This project depends on:

- SSE token streaming
- async model calls
- async ingestion pipeline steps
- long-lived open connections

WSGI would make these patterns much clumsier and less efficient.

## Threading vs async

### Async

Best for:

- network I/O
- file waits
- many simultaneous slow clients

Used in:

- OpenAI calls
- SSE responses
- LangGraph streaming

### Threads

Best for:

- blocking libraries that cannot be awaited
- CPU-bound work only when process/thread offload is acceptable

In this codebase, some operations are still effectively blocking even inside async handlers:

- Qdrant client calls
- some Docling work
- OCR and image conversion
- SQLAlchemy sync session usage

That means the code is async at the framework level, but not every internal step is non-blocking.

## Production note

Gunicorn uses `uvicorn.workers.UvicornWorker`, which means:

- Gunicorn manages multiple worker processes
- each worker hosts an ASGI event loop via Uvicorn

This is a common production pattern because it combines:

- process isolation
- async concurrency within each worker
- stable deployment ergonomics
