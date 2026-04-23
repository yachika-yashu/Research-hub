# 00 System Overview

## What the system actually is

ResearchHub is a multi-tenant research assistant built from these active runtime layers:

1. `dashboard.py` is the Streamlit frontend.
2. `main.py` starts a FastAPI ASGI app.
3. `app/core/graph.py` builds a LangGraph agent around `ChatOpenAI`.
4. Persistence is split across Qdrant, Redis, and a relational database layer.

The project now uses one default architecture across local and production:

- PostgreSQL via `DATABASE_URL`
- Redis exact cache via `REDIS_URL`
- Qdrant via `QDRANT_URL`
- LangGraph checkpoints via `CHECKPOINTS_DB_URL`

The only intended environment differences are:

- hostnames and credentials
- TLS, secrets, and infrastructure sizing
- persistent storage classes and AWS-managed services

If you want to understand the system by feature rather than by infrastructure, read [FUNCTIONALITY_MAP.md](./FUNCTIONALITY_MAP.md) next. That document links to rebuild-style flow docs for ingestion, retrieval, caching, auth, chat state, governance, and frontend execution.

So the live architecture is:

- Frontend: Streamlit with embedded HTML/CSS
- API: FastAPI
- Orchestration: LangGraph + LangChain tool binding
- LLM provider: OpenAI via `AsyncOpenAI` and `ChatOpenAI`
- Vector store: Qdrant service endpoint
- Exact cache: Redis
- Semantic cache: Qdrant
- Relational metadata and auth: PostgreSQL
- Session memory for graph threads: SQLite checkpoints
- Static media store: local filesystem under `assets/images`

## Component map

### Frontend

`dashboard.py` renders login, thread selection, PDF upload, and chat UI. It does not talk to databases directly. It calls FastAPI over HTTP and uses SSE for long-running operations:

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/token`
- `POST /api/v1/ingest`
- `POST /api/v1/query`
- `GET /api/v1/chat/threads`
- `GET /api/v1/chat/history/{thread_id}`

### FastAPI backend

`main.py` assembles the app and performs startup initialization:

- initializes Qdrant collection state
- initializes semantic cache collection
- initializes PostgreSQL-backed relational tables
- compiles the LangGraph graph
- attaches graph and checkpointer to `app.state`

FastAPI is the network boundary between UI and backend logic. It also owns auth, SSE streaming, and background governance logging.

### LangChain and LangGraph

`app/core/graph.py` defines a state graph with:

- state: `messages`, `tenant_id`
- node `agent`: calls `llm_with_tools.ainvoke(messages)`
- node `tools`: `ToolNode(tools)`
- conditional edge: `tools_condition`

This is a looped agent design rather than a one-shot chain. The model can decide to call:

- `rag_tool`
- `list_vault_papers_tool`
- `arxiv_search_tool`
- `auto_ingest_paper_tool`
- `python_repl_tool`

### Qdrant

Qdrant is the active retrieval database. It stores:

- dense vectors from OpenAI embeddings
- sparse vectors from `fastembed` BM25
- payload metadata including `tenant_id`, filename, year, authors, journal, chunk type, and media URL

It is also reused as the semantic response cache in a second collection named `researhub_semantic_cache`.

### Relational database and checkpoints

PostgreSQL stores:

1. `research` relational data
   - users
   - usage logs
   - trace logs

SQLite is still used for one job:

2. `checkpoints.db`
   - LangGraph thread checkpoints and chat history

That means the current system is not stateless. Conversation state still lives on disk until the checkpoint backend is moved to a shared service.

## High-level data flow

### Ingestion

1. User uploads a PDF in Streamlit.
2. Streamlit opens SSE connection to `/api/v1/ingest`.
3. FastAPI reads file bytes and calls `stream_process_ingestion`.
4. Docling extracts markdown and image crops.
5. Cleaning and chunking produce `DocumentChunk` objects.
6. OpenAI generates dense embeddings.
7. `fastembed` generates sparse BM25 vectors.
8. Qdrant upserts chunk points into `research_platform`.
9. PostgreSQL writes an ingest `UsageLog`.
10. SSE sends progress back to the browser.

### Query

1. User enters a prompt in Streamlit.
2. Streamlit opens SSE connection to `/api/v1/query`.
3. FastAPI authenticates user and determines `thread_id`.
4. FastAPI checks exact cache in Redis.
5. FastAPI checks semantic cache in Qdrant on exact-cache miss.
6. On cache miss, FastAPI runs LangGraph `astream_events`.
7. The graph streams model tokens and tool events.
8. Tools may search the vault, search arXiv, ingest a paper, or run Python.
9. FastAPI forwards streamed events to the UI.
10. After generation, FastAPI logs usage and trace data to PostgreSQL in a background task.
11. FastAPI stores the response in Redis exact cache and Qdrant semantic cache.

## Local vs intended production shape

Locally, developers should run the same backing services they plan to use in deployment:

- PostgreSQL
- Redis
- Qdrant

Filesystem-backed pieces that still remain:

- LangGraph checkpoints
- extracted image assets

This reduces local-to-production drift. The remaining major production gap is that checkpoints and extracted images are still local-disk oriented.
