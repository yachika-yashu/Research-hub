# ResearchHub Research Intelligence: Technical Roadmap & Deployment Guide

This document provides a detailed, step-by-step approach used to build the production-grade **ResearchHub Research Intelligence Platform**. It serves as both an architectural record and a deployment manual for ensuring the platform's multi-tenant research capabilities are fully utilized.

## 🏗️ System Architecture

The platform is built on a **High-Precision RAG (Retrieval-Augmented Generation)** architecture:

- **Frontend**: Streamlit-based "Research Control Console" with custom Glassmorphism UI.
- **Backend**: FastAPI Microservice managing orchestration, security, and LLM logic.
- **Vector Intelligence**: Qdrant (Hybrid Search: Dense + Sparse BM25 + Reranking).
- **Session Governance**: SQLite for user persistence and usage/trace logging.
- **Acceleration**: Redis for Semantic Caching of repeated complex research queries.

---

## 🗺️ The 50-Step Roadmap (Implementation Phases)

### Phase 1: Foundation & Extraction (Steps 1-15)
- [x] Initialized multi-modal ingestion pipeline using **Docling**.
- [x] Implemented **OCR-Fallback** (Tesseract) for scanned research papers.
- [x] Developed table and image extraction logic to maintain context for "scientific artifacts."

### Phase 2: Strategic Retrieval (Steps 16-30)
- [x] Configured **Hybrid Search** combining Dense embeddings (OpenAI) and Sparse vectors (BM25).
- [x] Integrated **Cross-Encoder Reranking** for top-K precision.
- [x] Implemented **Semantic Multi-Metadata Extraction** (Year, Authors, Journal) to allow deep filtering in the vault.

### Phase 3: Security & Multi-Tenancy (Steps 31-45)
- [x] Built a **Secure Auth System** with JWT tokens and salted passwords.
- [x] Implemented **Team-Based Vault Isolation**: Chunks are tagged with a unique `tenant_id` derived from the Team Code.
- [x] Hardened the retrieval logic to prevent any cross-tenant data leakage.

### Phase 4: Guardrails & Governance (Steps 46-48)
- [x] Developed the **"Rag-as-a-Judge" Verifier**: Every answer is scored for Faithfulness against retrieved sources.
- [x] Integrated **Token & Cost Tracking**: usage logs for billing and transparency.
- [x] Built the **Deep RAG Trace Debugger**: Clickable traces that show the exact prompt and chunks used by the AI.

### Phase 5: Production & Scale (Steps 49-50)
- [x] **Multi-Query Expansion**: Auto-generation of search variations for higher recall.
- [x] **Reciprocal Rank Fusion (RRF)**: Advanced merging of parallel search results.
- [x] **Semantic Redis Caching**: Optimized performance for recurring queries.
- [x] **Docker Orchestration**: Complete microservices stack ready for cloud deployment.
- [x] **Minimal Profiling & Benchmarking Toolkit**: Added repeatable API benchmarks and targeted hotspot profiling.

---

## 🚀 Step-by-Step Deployment Guide

Follow these steps to launch the platform in a production environment:

### Step 1: Environment Setup
Ensure you have a `.env` file in the root directory with the following keys:
```bash
OPENAI_API_KEY=your_key_here
SECRET_KEY=your_secure_random_string
QDRANT_PATH=./qdrant_storage
```

### Step 2: Launch via Docker (Recommended)
This command will build and orchestrate all four services (API, DB, Cache, Dashboard):
```bash
docker-compose up --build -d
```

### Step 3: Manual Installation (Developer Mode)
If running outside of Docker:
1. **API**: `uvicorn main:app --reload`
2. **Dashboard**: `streamlit run dashboard.py`
3. **Dependencies**: `pip install -r requirements.txt`

### Step 4: Verification
1. Login/Register a Team at `http://localhost:8501`.
2. Upload a research PDF in the "Ingest Paper" sidebar.
3. Once indexed, query the assistant.
4. Check the **Governance** tab to verify costs and **Trace** results.

---

## 🛡️ Reliability & Precision Guardrails

- **Fallback Search**: If Hybrid search fails, the system automatically retries with high-recall Dense-only search.
- **Precision Sorting**: Rerankers prioritize chunks that are semantically nearest to the *specific* query nuance.
- **Trust Badges**: Answers with low faithfulness scores are visually flagged to prevent misinformation.

---

**ResearchHub Research Intelligence is a complete, deployable ecosystem for collaborative research teams.**
