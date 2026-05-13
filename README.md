# ResearchHub

[![CI](https://github.com/yachika-yashu/Research-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/yachika-yashu/Research-hub/actions/workflows/ci.yml)

A production-grade AI research assistant for academics and engineers. Upload PDFs, chat with your papers, run literature reviews, monitor Arxiv — all in one self-hosted platform.

Built with FastAPI · LangGraph · Qdrant · Redis · PostgreSQL · Streamlit · Docker

---

## Features

| Feature | Description |
|---|---|
| **Conversational Chat** | Multi-turn Q&A over your papers with persistent thread memory |
| **Hybrid Search** | Dense + sparse (BM25) retrieval fused via RRF for best-of-both results |
| **Bulk Ingestion** | Upload multiple PDFs with real-time progress tracking |
| **Auto-Summary** | 5-sentence summary generated at ingest time |
| **Structured Extraction** | Contribution · dataset · baselines · limitations extracted per paper |
| **BibTeX Export** | One-click `.bib` file for your entire vault |
| **Literature Review** | AI-synthesised review across 2–5 papers, streamable and exportable |
| **Paper Comparison** | Side-by-side analysis of two papers on a specific question |
| **Knowledge Graph** | Visual map of paper relationships by shared authors and keywords |
| **Passage Search** | Raw vector retrieval — no LLM, just the exact matching chunks |
| **Reading Queue** | Track papers to read before they enter the vault |
| **Arxiv Monitoring** | Daily keyword watches — new papers alert you automatically |
| **Notes Editor** | Per-user Markdown notes with live preview |
| **Usage Dashboard** | Token usage, cost tracking, and faithfulness metrics |
| **Multi-tenancy** | Team-based isolation — each team sees only its own vault |

See [FEATURES.md](FEATURES.md) for detailed examples of each feature.

---

## Architecture

```
Browser
  │
  ▼
Streamlit Dashboard (port 8501)
  │  server-side HTTP calls
  ▼
FastAPI (port 8000)
  ├── LangGraph agent  ──► OpenAI GPT-4o-mini
  ├── Qdrant           ──► hybrid vector search (dense + BM25)
  ├── PostgreSQL       ──► users · notes · summaries · checkpoints
  └── Redis            ──► exact cache · Pub/Sub for job progress

arq Worker (background)
  ├── PDF extraction (docling + pytesseract fallback)
  ├── Chunking · embedding · Qdrant upsert
  ├── LLM summary + structured field extraction
  └── Arxiv daily monitor cron
```

---

## Stack

- **Backend:** FastAPI, LangGraph, LangChain
- **AI:** OpenAI `gpt-4o-mini` + `text-embedding-3-small`
- **Vector DB:** Qdrant (hybrid dense + sparse search)
- **Relational DB:** PostgreSQL (via SQLAlchemy + psycopg3)
- **Cache:** Redis (exact-match + semantic two-layer cache)
- **Task Queue:** arq (async Redis-backed workers)
- **Frontend:** Streamlit
- **Containers:** Docker Compose

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2
- An [OpenAI API key](https://platform.openai.com/api-keys)

That's it. Everything else (Postgres, Qdrant, Redis) runs in Docker.

---

## Quick Start

Or pull the pre-built image (no build step needed):
```bash
docker pull ghcr.io/yachika-yashu/research-hub:latest
```

```bash
# 1. Clone
git clone https://github.com/yachika-yashu/Research-hub
cd research-hub

# 2. Configure
cp .env.example .env
# Open .env and set:
#   OPENAI_API_KEY=sk-...
#   JWT_SECRET_KEY=<run: python -c "import secrets; print(secrets.token_hex(32))">

# 3. Run
docker compose up -d --build

# 4. Open
# Dashboard → http://localhost:8501
# API docs  → http://localhost:8000/docs
```

Register an account on first visit. The team code you enter groups users into the same vault.

---

## Environment Variables

Copy `.env.example` to `.env`. Required variables:

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | Your OpenAI API key |
| `JWT_SECRET_KEY` | Random secret for signing JWTs — generate with `secrets.token_hex(32)` |
| `POSTGRES_PASSWORD` | Password for the local Postgres container |

All other variables have working defaults for local Docker development.

Optional:

| Variable | Description |
|---|---|
| `LANGCHAIN_API_KEY` | Enables LangSmith tracing for debugging the agent |
| `GOOGLE_CLIENT_ID/SECRET` | Enables Google SSO login |

---

## Project Structure

```
research-hub/
├── .github/
│   └── workflows/
│       └── ci.yml           # Lint + test + GHCR image push
├── app/
│   ├── api/
│   │   ├── routes.py        # All REST endpoints
│   │   └── auth.py          # JWT + Google SSO
│   ├── core/
│   │   ├── config.py        # All env var loading
│   │   ├── database.py      # SQLAlchemy models
│   │   ├── graph.py         # LangGraph agent definition
│   │   ├── logic.py         # Chunking, embedding, BibTeX utils
│   │   ├── cache.py         # Two-layer cache (Redis + Qdrant)
│   │   ├── guardrails.py    # Pre-retrieval query filtering
│   │   ├── globals.py       # Shared singletons (clients, pools)
│   │   ├── logging.py       # Structured logging setup
│   │   ├── qdrant.py        # Qdrant client + collection init
│   │   ├── redis.py         # Redis client init
│   │   └── auth.py          # Auth helpers (token decode, user lookup)
│   ├── schemas/
│   │   ├── models.py        # Pydantic request/response models
│   │   └── auth.py          # Auth-specific schemas
│   ├── services/
│   │   ├── ingestion.py     # PDF → chunks → Qdrant pipeline
│   │   ├── extractor.py     # docling + OCR text extraction
│   │   ├── vector_store.py  # Qdrant hybrid search
│   │   └── tools.py         # LangGraph tool definitions
│   └── worker.py            # arq task definitions + Arxiv cron
├── tests/
│   └── test_logic.py        # Unit tests (no external services)
├── dashboard.py             # Streamlit UI
├── main.py                  # FastAPI app entry point
├── docker-compose.yml       # Development setup
├── docker-compose.prod.yml  # Production setup (with Caddy HTTPS)
├── Caddyfile                # Reverse proxy + TLS config
├── Makefile                 # Dev shortcuts (up, down, logs, shell…)
├── Dockerfile
├── requirements.txt
├── LICENSE
└── .env.example
```

---

## Production Deployment

A production-ready Docker Compose with Caddy (automatic HTTPS) is included:

```bash
cp .env.example .env.production
# Fill in .env.production — especially OPENAI_API_KEY, JWT_SECRET_KEY,
# POSTGRES_PASSWORD, and your domain in ALLOWED_ORIGINS / TRUSTED_HOSTS.
# Update Caddyfile with your actual domain.

docker compose -f docker-compose.prod.yml up -d --build
```

See [Caddyfile](Caddyfile) and [docker-compose.prod.yml](docker-compose.prod.yml) for details. DNS must point to your server before Caddy can issue TLS certificates.

---

## License

MIT — see [LICENSE](LICENSE).
