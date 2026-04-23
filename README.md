# ResearchHub

A production-grade, multi-tenant **Collaborative Research Intelligence Platform** powered by advanced Retrieval-Augmented Generation (RAG), LangGraph state machines, and semantic caching.

## 🎯 Overview

ResearchHub is a complete research assistant system designed for teams to:
- **Ingest** research papers, documents, and data sources
- **Index** content using semantic embeddings and hybrid search
- **Query** with an AI agent that retrieves and synthesizes information
- **Collaborate** with multi-tenant support and role-based access control
- **Cache** responses intelligently to reduce API costs

### Key Features

✨ **Multi-Tenant Architecture** - Isolated workspaces for teams and organizations  
🤖 **LangGraph Agent** - Stateful AI reasoning with tool integration  
⚡ **Semantic + Exact Caching** - Redis + Qdrant dual-cache strategy  
🔐 **Production Security** - JWT authentication, environment isolation  
📊 **Real-time Dashboard** - Streamlit-based research control console  
🚀 **Fully Containerized** - Docker Compose setup for local/production  

## 🏗️ System Architecture

```
Frontend (Streamlit)
    ↓
FastAPI API Server
    ↓
LangGraph Agent ← ChatOpenAI (LLM)
    ↓ ↓ ↓
Qdrant (Vectors) | Redis (Cache) | PostgreSQL (Metadata)
```

### Core Components

| Component | Purpose |
|-----------|---------|
| **dashboard.py** | Streamlit frontend with auth, chat, document uploads |
| **main.py** | FastAPI ASGI application with lifecycle management |
| **app/core/graph.py** | LangGraph agent definition and compilation |
| **app/core/cache.py** | Dual-layer caching (exact + semantic) |
| **app/core/qdrant.py** | Vector database integration |
| **app/services/ingestion.py** | Document processing pipeline |

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Docker & Docker Compose (optional, recommended for services)
- OpenAI API key

### Local Setup

1. **Clone and setup environment:**
   ```bash
   git clone https://github.com/yourusername/researhub.git
   cd researhub
   python -m venv venv
   source venv/Scripts/activate  # Windows: venv\Scripts\activate
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys and database credentials
   ```

4. **Start services (Docker Compose recommended):**
   ```bash
   docker-compose up -d
   ```

5. **Run migrations and start servers:**
   ```bash
   # In separate terminals:
   python main.py          # API on http://localhost:8000
   streamlit run dashboard.py  # Dashboard on http://localhost:8501
   ```

## 📖 Documentation

Comprehensive documentation is available in the `docs/` directory:

- [00_SYSTEM_OVERVIEW.md](docs/00_SYSTEM_OVERVIEW.md) - System architecture and components
- [01_REQUEST_LIFECYCLE.md](docs/01_REQUEST_LIFECYCLE.md) - How requests flow through the system
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - Detailed system design
- [DEPLOYMENT_PRODUCTION.md](docs/13_DEPLOYMENT_PRODUCTION.md) - Production deployment guide
- [FUNCTIONALITY_MAP.md](docs/FUNCTIONALITY_MAP.md) - Feature capabilities by file

## 🔧 Configuration

### Environment Variables

```env
# OpenAI
OPENAI_API_KEY=sk-...

# Database
DATABASE_URL=postgresql://researhub:password@localhost:5432/researhub
POSTGRES_DB=researhub
POSTGRES_USER=researhub

# Redis
REDIS_URL=redis://localhost:6379/0

# Qdrant
QDRANT_URL=http://localhost:6333

# Security
JWT_SECRET_KEY=your_long_random_secret_here

# LangChain (optional)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls...
LANGCHAIN_PROJECT=researhub-project
```

See [.env.example](.env.example) for all configuration options.

## 🐳 Docker Deployment

### Local Development with Docker Compose

```bash
docker-compose up -d
```

This starts:
- PostgreSQL database
- Redis cache
- Qdrant vector database
- ResearchHub API (port 8000)

### Production Deployment

See [docs/13_DEPLOYMENT_PRODUCTION.md](docs/13_DEPLOYMENT_PRODUCTION.md) for:
- AWS ECR/ECS setup
- Environment configuration
- Scaling strategies
- Monitoring and logging

## 🔐 Security

- **Environment Isolation**: API keys stored only in `.env` (never committed)
- **JWT Authentication**: Secure multi-tenant access control
- **Database Credentials**: Environment-based, rotatable
- **CORS & TrustedHost**: Configurable security headers
- **Rate Limiting**: Ready for production rate limiting

### Pre-deployment Checklist

- [ ] Rotate `JWT_SECRET_KEY` in production
- [ ] Use strong database passwords
- [ ] Enable HTTPS/TLS in production
- [ ] Configure CORS for your domain
- [ ] Set appropriate database backups
- [ ] Enable monitoring and logging

## 📊 Performance

- **Semantic Caching**: Reduces embedding API calls by ~70%
- **Exact Query Cache**: Instant responses for repeated queries
- **Hybrid Search**: BM25 + Dense vectors in Qdrant
- **Connection Pooling**: SQLAlchemy + Redis connections

See [docs/12_PERFORMANCE_AND_SCALING.md](docs/12_PERFORMANCE_AND_SCALING.md) for optimization details.

## 🧪 Testing & Development

### Run Tests

```bash
pytest tests/
```

### Profile Performance

```bash
python perf/profile_hotspots.py
python perf/benchmark_api.py
```

### Development Setup

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run with auto-reload
uvicorn main:app --reload
streamlit run dashboard.py
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make changes and test
4. Commit: `git commit -m 'Add amazing feature'`
5. Push: `git push origin feature/amazing-feature`
6. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see [LICENSE](LICENSE) file for details.

## 🙋 Support

For questions and support:
- Open an issue on GitHub
- Check existing documentation in `docs/`
- Review error logs in `logs/` directory

## 🗺️ Roadmap

- [ ] Advanced analytics dashboard
- [ ] Multi-modal document support (images, audio)
- [ ] Fine-tuned embedding models
- [ ] Distributed caching
- [ ] Advanced observability features
- [ ] Kubernetes deployment templates

---

**Built with:** FastAPI · LangChain · LangGraph · Qdrant · Redis · PostgreSQL · Streamlit
