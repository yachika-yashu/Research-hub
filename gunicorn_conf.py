import multiprocessing
import os

# Gunicorn configuration for ResearchHub Production
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# UvicornWorker is async — each worker already handles thousands of concurrent
# connections via asyncio. The 2×CPU+1 formula is designed for sync (gevent/gthread)
# workers and causes excessive memory use with async ones.
# Recommended for async: 1–2 workers per CPU core, capped at 4 for RAG workloads
# (each worker holds its own LangGraph graph + DB connection pool in memory).
_cpu = multiprocessing.cpu_count()
workers = int(os.getenv("GUNICORN_WORKERS", min(_cpu, 4)))
worker_class = "uvicorn.workers.UvicornWorker"

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Timeout
timeout = 120
keepalive = 5
