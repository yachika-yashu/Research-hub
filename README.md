# ResearchHub

[![CI](https://github.com/yachika-yashu/Research-hub/actions/workflows/ci.yml/badge.svg)](https://github.com/yachika-yashu/Research-hub/actions/workflows/ci.yml)
[![Benchmark Gate](https://github.com/yachika-yashu/Research-hub/actions/workflows/benchmark_gate.yml/badge.svg)](https://github.com/yachika-yashu/Research-hub/actions/workflows/benchmark_gate.yml)

A production-grade AI research assistant for academics and engineers. Upload PDFs, chat with your papers, run literature reviews, monitor Arxiv — all in one self-hosted platform..

Built with FastAPI · LangGraph · Qdrant · Redis · PostgreSQL · Streamlit · Docker

---
## Deep Dive: End-to-End Agentic AI Platform (Demo Walkthrough)

Explore the full system architecture, agent orchestration, and production design.
[![Watch Video](assets/images/1.jpg)](https://www.youtube.com/watch?v=pc9u-_JEkc8)


---
## Features

| Feature | Description |
|---|---|
| **Conversational Chat** | Multi-turn Q&A over your papers with persistent thread memory (PostgreSQL-backed LangGraph checkpoints) |
| **Hybrid Search** | Dense + sparse (BM25) retrieval fused via RRF, then re-ranked by a cross-encoder for maximum precision |
| **Two-layer Cache** | Exact Redis cache for identical queries + semantic Qdrant cache for near-duplicates |
| **Bulk Ingestion** | Upload multiple PDFs with real-time SSE progress bar — Docling extraction, figures saved as images |
| **Auto-Summary** | 5-sentence paper summary generated at ingest time and stored in PostgreSQL |
| **Structured Extraction** | Contribution · dataset · baselines · limitations extracted per paper |
| **Bibliography Extraction** | Full reference list extracted with arXiv IDs for one-click ingestion of cited papers |
| **BibTeX Export** | One-click `.bib` file for your entire vault with auto-generated citation keys |
| **Literature Review** | AI-synthesised review across 2–5 papers, streamable and exportable as Markdown |
| **Paper Comparison** | Side-by-side analysis of two papers on a specific question |
| **Knowledge Graph** | Visual map of paper relationships by shared authors and keywords (Plotly interactive) |
| **Passage Search** | Raw vector retrieval — no LLM, just the exact matching chunks |
| **Reading Queue** | Track papers to read before they enter the vault |
| **Arxiv Monitoring** | Daily keyword watches via arq cron — new papers alert you automatically |
| **Notes Editor** | Per-user Markdown notes with live preview |
| **RAGAS Evaluation** | Every query response is automatically scored for faithfulness using RAGAS |
| **Usage Dashboard** | Token usage, cost tracking, faithfulness metrics, and activity timeline |
| **Multi-tenancy** | Team-based isolation — each team sees only its own vault |
| **Pre-retrieval Guardrails** | LLM-as-judge safety filter blocks prompt injection and off-topic queries |
| **MLflow Tracking** | Benchmark runs logged to MLflow with git commit tags |

See [FEATURES.md](FEATURES.md) for detailed examples of each feature.

---

## Architecture

```
Browser
  │
  ▼
Streamlit Dashboard (port 8501)
  │  server-side HTTP + SSE
  ▼
FastAPI (port 8000)
  ├── LangGraph agent (5 tools) ──► OpenAI GPT-4o-mini
  ├── Pre-retrieval guardrail   ──► LLM safety check before any search
  ├── Two-layer cache           ──► Redis (exact) + Qdrant (semantic)
  ├── Qdrant                    ──► hybrid search (dense + BM25 + RRF + cross-encoder)
  ├── PostgreSQL                ──► users · notes · summaries · LangGraph checkpoints
  └── Redis                     ──► exact cache · Pub/Sub for job progress

arq Worker (background)
  ├── PDF extraction (Docling layout-aware + pytesseract OCR fallback)
  ├── Figure extraction → PNG saved to /assets/images/
  ├── Chunking · dense embedding · BM25 sparse encoding · Qdrant upsert
  ├── LLM: summary + structured field extraction + bibliography extraction
  ├── RAGAS faithfulness evaluation on every query response
  └── Arxiv daily keyword monitor cron (07:00 UTC)

MLflow (port 5000)
  └── Benchmark run tracking with git commit tags + drift reports
```

---

## Stack

| Layer | Technology |
|---|---|
| Backend API | FastAPI + Uvicorn |
| AI Agent | LangGraph (stateful, multi-tool, PostgreSQL checkpoints) |
| LLM | OpenAI `gpt-4o-mini` (generation) · `gpt-4o` for complex queries |
| Embeddings | OpenAI `text-embedding-3-small` (768-dim Matryoshka) |
| Vector DB | Qdrant — hybrid dense + sparse search, RRF fusion |
| Sparse Index | BM25 via `fastembed` |
| Reranking | `BAAI/bge-reranker-base` cross-encoder |
| Evaluation | RAGAS faithfulness + answer relevancy |
| Relational DB | PostgreSQL via SQLAlchemy + psycopg3 |
| Cache & Queue | Redis — exact cache · arq job queue · pub/sub |
| PDF Extraction | Docling (layout-aware) + pytesseract (OCR fallback) |
| Frontend | Streamlit |
| Experiment tracking | MLflow |
| Containers | Docker Compose |
| Production proxy | Caddy (automatic HTTPS) |
| CI | GitHub Actions — lint · test · GHCR image push · benchmark gate |

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2
- An [OpenAI API key](https://platform.openai.com/api-keys)

That's it. PostgreSQL, Qdrant, Redis, and MLflow all run inside Docker.

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/yachika-yashu/Research-hub
cd research-hub

# 2. Configure
cp .env.example .env
# Edit .env and set at minimum:
#   OPENAI_API_KEY=sk-...
#   JWT_SECRET_KEY=<run: python -c "import secrets; print(secrets.token_hex(32))">
#   POSTGRES_PASSWORD=<any strong password>

# 3. Start
docker compose up -d --build

# 4. Open
# Dashboard  → http://localhost:8501
# API docs   → http://localhost:8000/docs
# MLflow     → http://localhost:5000
```

Register on first visit. The **team code** you enter groups users into a shared vault — everyone on the same team sees the same papers.

**Or pull the pre-built image** (skips the build step):

```bash
docker pull ghcr.io/yachika-yashu/research-hub:latest
docker compose up -d
```

---

## Environment Variables

Copy `.env.example` to `.env`. Required:

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | Your OpenAI API key |
| `JWT_SECRET_KEY` | Random secret for signing JWTs — generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `POSTGRES_PASSWORD` | Password for the local Postgres container |

All other variables have working defaults for local Docker development. Optional extras:

| Variable | Description |
|---|---|
| `LANGCHAIN_API_KEY` | Enables LangSmith tracing for the AI agent and benchmark runs |
| `LANGCHAIN_TRACING_V2` | Set to `true` when using LangSmith |
| `LANGCHAIN_PROJECT` | LangSmith project name (default: `researchhub-benchmarking`) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Enables Google SSO login |
| `GUNICORN_WORKERS` | Override number of Uvicorn workers in production (default: `min(cpu_count, 4)`) |

---

## Makefile shortcuts

```bash
make build        # docker compose up -d --build
make up           # docker compose up -d (no rebuild)
make down         # stop all services
make logs         # tail all logs
make logs-api     # tail API logs only
make shell-api    # bash into the API container
make shell-worker # bash into the worker container
make prod-up      # start production stack (Caddy + HTTPS)
make clean        # docker compose down -v (removes volumes)
```

---

## Running benchmarks

A complete MLOps benchmarking suite is included.

```bash
# Quick smoke test (HTTP-based, no local app deps needed)
python benchmarking/smoke_test.py

# Run eval dataset (quick mode: 5 questions)
python benchmarking/eval_runner.py --quick

# Save first baseline for regression tracking
python benchmarking/baseline_manager.py save --file benchmarking/results/eval_results.json

# Full benchmark (quality · latency · cost · safety · worker)
python benchmarking/run_benchmark.py

# Save the full benchmark result as baseline
python benchmarking/run_benchmark.py --save-baseline

# Compare next run vs saved baseline (shows regression report)
python benchmarking/run_benchmark.py

# CI gate check (what GitHub Actions runs on every PR)
python benchmarking/check_gate.py results.json latency.json benchmarking/thresholds.json
```

---

## Project Structure

```
research-hub/
├── .github/
│   └── workflows/
│       ├── ci.yml                   # Lint · unit tests · GHCR image push
│       └── benchmark_gate.yml       # Eval · benchmark · regression gate on every PR
├── app/
│   ├── api/
│   │   ├── routes.py                # All REST endpoints
│   │   └── auth.py                  # JWT + Google SSO
│   ├── core/
│   │   ├── config.py                # All env var loading
│   │   ├── database.py              # SQLAlchemy models (User, UsageLog, TraceLog, etc.)
│   │   ├── graph.py                 # LangGraph agent + 5 tool definitions
│   │   ├── logic.py                 # Chunking · embedding · RAGAS · BibTeX utils
│   │   ├── cache.py                 # Two-layer cache (exact Redis + semantic Qdrant)
│   │   ├── guardrails.py            # Pre-retrieval LLM safety filter
│   │   ├── globals.py               # Shared singletons (OpenAI client)
│   │   ├── logging.py               # Structured logging setup
│   │   ├── qdrant.py                # Qdrant client singleton
│   │   ├── redis.py                 # Redis async client singleton
│   │   └── auth.py                  # JWT decode, user lookup, bcrypt helpers
│   ├── schemas/
│   │   ├── models.py                # Pydantic request/response models
│   │   └── auth.py                  # Auth-specific schemas
│   ├── services/
│   │   ├── ingestion.py             # 10-stage PDF → Qdrant pipeline (streaming)
│   │   ├── extractor.py             # Docling + figure extraction + OCR fallback
│   │   ├── vector_store.py          # Qdrant hybrid search (dense + BM25 + RRF + reranker)
│   │   └── tools.py                 # LangGraph tool definitions
│   └── worker.py                    # arq task definitions + Arxiv daily cron
├── benchmarking/
│   ├── run_benchmark.py             # Full benchmark suite runner
│   ├── smoke_test.py                # HTTP-based quick health check
│   ├── eval_runner.py               # Eval dataset runner (HTTP, no local deps)
│   ├── check_gate.py                # CI gate + local orchestrator (dual mode)
│   ├── baseline_manager.py          # Baseline save/load/regression compare
│   ├── quality_evaluator.py         # RAGAS faithfulness + answer relevancy
│   ├── latency_profiler.py          # Stage-level timing + percentiles
│   ├── cost_tracker.py              # UsageLog cost breakdown + cache savings
│   ├── safety_evaluator.py          # 20-probe injection battery + PII scan
│   ├── data_quality.py              # Qdrant chunk audit (OCR, truncation, bleed)
│   ├── worker_benchmarker.py        # arq job timing via Redis pub/sub
│   ├── ab_tester.py                 # Paired t-test prompt variant comparison
│   ├── scorecard.py                 # PASS/FAIL scorecard across 5 dimensions
│   ├── unified_logger.py            # MLflow + LangSmith + Evidently drift
│   ├── model_router.py              # Heuristic query complexity classifier
│   ├── thresholds.json              # Gate thresholds (faithfulness, latency, cost)
│   └── eval_dataset.json            # 10 structural eval questions
├── perf/
│   ├── locustfile.py                # Load testing with Locust
│   ├── benchmark_api.py             # API timing harness
│   └── profile_hotspots.py          # cProfile hot-path analysis
├── tests/
│   └── test_logic.py                # Unit tests (no external services required)
├── dashboard.py                     # Streamlit UI (entire frontend)
├── main.py                          # FastAPI app entry point
├── docker-compose.yml               # Development setup (7 services)
├── docker-compose.prod.yml          # Production setup (with Caddy HTTPS)
├── Caddyfile                        # Reverse proxy + automatic TLS config
├── Makefile                         # Dev shortcuts
├── Dockerfile
├── gunicorn_conf.py                 # Production Gunicorn config
├── requirements.txt
├── LICENSE
└── .env.example
```

---

## Production Deployment

A production-ready Docker Compose with Caddy (automatic HTTPS) is included:

```bash
cp .env.example .env.production
# Edit .env.production:
#   OPENAI_API_KEY, JWT_SECRET_KEY, POSTGRES_PASSWORD (required)
#   ALLOWED_ORIGINS, TRUSTED_HOSTS (set your domain)
#   APP_ENV=production

# Update Caddyfile with your actual domain name

make prod-up
# or: docker compose -f docker-compose.prod.yml up -d --build
```

DNS must point to your server before Caddy can issue TLS certificates. See [Caddyfile](Caddyfile) and [docker-compose.prod.yml](docker-compose.prod.yml) for configuration details.

---

## License

MIT — see [LICENSE](LICENSE).
