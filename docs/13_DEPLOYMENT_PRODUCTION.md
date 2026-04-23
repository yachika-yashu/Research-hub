# 13 Deployment Production

## What the current deployment artifacts do

The repo already contains:

- `Dockerfile`
- `docker-compose.yml`
- `gunicorn_conf.py`

Those files now describe the default stack the project should use in both local and production environments:

- Qdrant is real
- Streamlit is real
- FastAPI is real
- Redis exact cache is wired
- PostgreSQL is included in compose

## Backend deployment step by step

### 1. Build image

`Dockerfile`:

- starts from `python:3.11-slim`
- installs system packages for PDF/OCR work
- installs Python requirements
- copies source
- runs Gunicorn with Uvicorn workers

Why Gunicorn is used:

- manages multiple worker processes
- better production process model than plain `uvicorn --reload`

### 2. Start FastAPI service

Container command:

```bash
gunicorn -c gunicorn_conf.py main:app
```

Why:

- `main:app` exposes ASGI application object
- worker class is `uvicorn.workers.UvicornWorker`

### 3. Configure environment variables

At minimum:

- `OPENAI_API_KEY`
- `JWT_SECRET_KEY`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI`
- `PORT`

For all serious environments, configure:

- `QDRANT_URL`
- `DATABASE_URL`
- `REDIS_URL`
- object storage credentials

### 4. Mount persistent volumes

Current code requires persistence for:

- `qdrant_storage/`
- `research.db`
- `checkpoints.db`
- `assets/images/`
- `logs/`

Without volumes, container restarts lose critical data.

## Redis deployment

### Current state

Compose launches Redis and the app uses it through `REDIS_URL` for exact cache.

### If expanded in production

Redis is also the natural place for:

- queues
- ephemeral session coordination
- rate limits

## PostgreSQL deployment

### Current state

Compose launches PostgreSQL and the app uses it through `DATABASE_URL`.

### Recommended production migration

1. point `DATABASE_URL` at managed PostgreSQL
2. run schema migration tooling
3. move audit/auth tables to PostgreSQL in production
4. consider LangGraph checkpoint migration if shared state is needed

Why this matters:

- concurrent production traffic and multi-replica APIs need shared durable DB semantics

## Frontend deployment

### Streamlit

Compose runs:

```bash
streamlit run dashboard.py --server.port=8501 --server.address=0.0.0.0
```

Requirements:

- `API_BASE_URL` must point to FastAPI service
- Streamlit should sit behind a reverse proxy in production

## Reverse proxy and Nginx

### Why a reverse proxy is needed

- TLS termination
- stable external routing
- header forwarding
- request size limits for PDF upload
- better buffering and timeout control for SSE

### Typical Nginx responsibilities

- route `/api/` to FastAPI
- route `/` to Streamlit
- preserve SSE behavior by disabling inappropriate buffering on streaming endpoints

Important for SSE:

- proxy/read timeouts must allow long responses
- buffering settings should not delay token delivery

## Docker usage

### Current compose topology

- `postgres`
- `qdrant`
- `redis`
- `api`
- `dashboard`

### Production improvements

- add named volumes for persistent data
- externalize secrets instead of plain `.env` on disk where possible
- add health checks
- separate dev and prod compose profiles or manifests
- run the benchmark harness before and after major infra changes to capture a deployment baseline

## Secrets management

Do not hardcode:

- OpenAI keys
- JWT secret
- OAuth secrets

Use:

- cloud secret manager
- container orchestration secret injection
- environment variables populated at deploy time

## Recommended production topology

### Minimum viable

- Nginx or cloud load balancer
- FastAPI container
- Streamlit container
- Qdrant persistent service
- PostgreSQL
- Redis
- object storage for extracted images

### Better

- multiple FastAPI replicas
- Redis for exact cache and background coordination
- worker service for ingestion jobs
- centralized logging and metrics
