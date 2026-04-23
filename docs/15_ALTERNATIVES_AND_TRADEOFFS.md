# 15 Alternatives And Tradeoffs

## FastAPI vs Flask

### Why FastAPI fits this project

- first-class async
- built-in request validation with Pydantic
- clean dependency injection
- natural fit for SSE and typed APIs

### Tradeoff

- more abstraction than Flask
- async misuse can create false confidence if internals remain blocking

### Why Flask would be weaker here

- streaming and async patterns are less natural
- more manual validation and dependency plumbing

## Redis vs in-memory cache

### In-memory cache

Pros:

- simplest
- fastest per-process lookup

Cons:

- not shared across workers
- lost on restart
- unsuitable for multi-replica deployments

### Redis

Pros:

- shared cache
- TTL support
- good for ephemeral distributed state

Cons:

- extra service to operate
- not ideal as a durable source of truth

### What this project does now

It uses both:

- Redis for exact-match cache
- Qdrant for semantic cache

Tradeoff:

- stronger semantic reuse through Qdrant
- cheaper identical-prompt reuse through Redis
- slightly more complexity because two cache tiers must stay coherent

## PostgreSQL vs NoSQL

### PostgreSQL

Pros:

- ACID transactions
- strong relational modeling
- robust indexing
- good fit for users, audit logs, traces

Cons:

- schema discipline required
- extra operational surface compared with SQLite

### NoSQL

Pros:

- flexible schema
- sometimes easier horizontal write scale

Cons:

- weaker fit for relational auth and audit data
- transactions and reporting often get messier

### Best fit here

PostgreSQL is the better production choice because the durable metadata is relational and tenant-scoped.

It is also the better local-development choice when you want minimal drift between laptop, CI, and production.

## LangChain vs direct API

### LangChain

Pros:

- model wrappers
- tool abstraction
- easier LangGraph integration
- cleaner message handling

Cons:

- extra abstraction layer
- debugging can require understanding framework internals

### Direct OpenAI API

Pros:

- fewer layers
- maximum control
- easier to reason about exact provider payloads

Cons:

- must hand-build tool schema handling and orchestration glue
- more custom code around messages and dispatch

### Best fit here

Because the system is explicitly agentic and tool-driven, LangChain plus LangGraph is a reasonable choice.

## Qdrant vs simpler vector stores

### Why Qdrant fits

- hybrid dense+sparse support
- payload filtering
- local deployment option

### Tradeoff

- more operational complexity than very lightweight local stores

### Why it matters here

Research retrieval benefits from hybrid search and metadata filters more than many toy RAG apps do.
