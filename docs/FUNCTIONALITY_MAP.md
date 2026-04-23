# Functionality Map

This document is the entry point for understanding the project by capability rather than by file name.

If you are rebuilding ResearchHub from scratch, do not start by reading files in arbitrary order. Start from the functionality you want to reconstruct, then follow the file flow for that capability.

## Recommended reading order

1. [01_INGESTION_FLOW.md](./functionalities/01_INGESTION_FLOW.md)
2. [02_RETRIEVAL_AND_INDEXING_FLOW.md](./functionalities/02_RETRIEVAL_AND_INDEXING_FLOW.md)
3. [03_QUERY_AND_AGENT_FLOW.md](./functionalities/03_QUERY_AND_AGENT_FLOW.md)
4. [04_REDIS_CACHE_FLOW.md](./functionalities/04_REDIS_CACHE_FLOW.md)
5. [05_AUTH_AND_MULTI_TENANCY_FLOW.md](./functionalities/05_AUTH_AND_MULTI_TENANCY_FLOW.md)
6. [06_CHAT_STATE_AND_HISTORY_FLOW.md](./functionalities/06_CHAT_STATE_AND_HISTORY_FLOW.md)
7. [07_GOVERNANCE_AND_OBSERVABILITY_FLOW.md](./functionalities/07_GOVERNANCE_AND_OBSERVABILITY_FLOW.md)
8. [08_FRONTEND_EXECUTION_FLOW.md](./functionalities/08_FRONTEND_EXECUTION_FLOW.md)

## How to use these docs

Each functionality document answers the same questions:

- What problem was being solved?
- What came first in the design thinking?
- Which dependencies were required?
- Which files were created or touched?
- What is the runtime execution flow?
- What state is read and written?
- What would you rebuild first if starting over?

## File ownership map

Use this as the quick file-to-responsibility lookup.

### Application bootstrap

- `main.py`
- `app/core/config.py`
- `app/core/logging.py`

### Authentication and tenant isolation

- `app/api/auth.py`
- `app/core/auth.py`
- `app/core/database.py`
- `app/schemas/auth.py`

### Ingestion and extraction

- `app/api/routes.py`
- `app/services/ingestion.py`
- `app/services/extractor.py`
- `app/core/logic.py`
- `app/schemas/models.py`

### Retrieval and vector storage

- `app/services/vector_store.py`
- `app/core/qdrant.py`
- `app/core/globals.py`

### Agent orchestration

- `app/core/graph.py`
- `app/services/tools.py`
- `app/api/routes.py`

### Cache

- `app/core/redis.py`
- `app/core/cache.py`
- `app/api/routes.py`
- `app/services/ingestion.py`

### Conversation history and checkpoints

- `main.py`
- `app/core/graph.py`
- `app/api/routes.py`

### Governance and logging

- `app/core/logging.py`
- `app/core/database.py`
- `app/api/routes.py`

### Frontend

- `dashboard.py`

## Important design rule

The project is easiest to understand as a sequence of flows, not as a hierarchy of folders.

The most important flows are:

1. PDF upload and ingestion
2. Query request and agent execution
3. Cache hit and cache invalidation
4. Authentication and tenant-scoped access
5. Conversation state persistence

That is why the detailed functionality docs are organized around flows instead of modules.
