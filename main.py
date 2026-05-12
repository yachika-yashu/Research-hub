# =============================================================================
# Research Intelligence Platform — main.py
# =============================================================================
# VERSION: v1.1.2 (Collaborative Auth & Multi-Tenancy)
# =============================================================================
# This is the backend (brain)that starts everything, connects all systems, and exposes APIs
import os # Used to interact with the operating system, like creating folders or managing files
from contextlib import asynccontextmanager # Used to manage the lifecycle of the application like startup & shutdown cleanly
from fastapi import FastAPI # Used to create the FastAPI application
from fastapi.staticfiles import StaticFiles # Used to serve static files, like images or CSS files
from starlette.middleware.trustedhost import TrustedHostMiddleware # Used to add security headers to the application, prevent host header attacks
from arq import create_pool # asynch redis queue,Used for background jobs Example: long-running AI tasks, indexing, etc.
from app.worker import redis_settings # Used to configure the Redis settings for the application also used by ARQ (Task Queue)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver # Used to save the state of the LangGraph application, Stores graph state in PostgreSQL, Used for checkpointing (saving progress)
from psycopg_pool import AsyncConnectionPool # used to create a pool of database connections
from app.api.routes import router as api_router # function From app/api/routes.py to define the API routes for the application, Route definitions for /query, /ingest, /debug,/stats,/chat, /health
from app.api.auth import router as auth_router # function From app/api/auth.py to define the authentication routes for the application login/signup, login (JWT token generation), Google SSO, register,
from app.core.cache import init_cache_db # function from app/core/cache.py to initialize the cache database for semantic caching
from app.core.database import init_db as init_user_db # function from app/core/database.py t initialize the PostgreSQL database for user management and collaboration  (renamed from init_db to avoid conflict)
from app.core.config import ( # go tp app folder-> its subfolder core -> config file in itfind below values in that file 
    ASSETS_DIR, # Used to define the directory where assets are store,in our case images from research papers
    CHECKPOINTS_DB_URL, # Used to define the URL for the checkpoints database,we are using postgresql database for checkpointing
    ALLOWED_ORIGINS, # Used to define the allowed origins for the application, Cross-Origin Resource Sharing (CORS)
    TRUSTED_HOSTS, # Used to define the trusted hosts for the application, Prevents host header attacks.
    ENABLE_DOCS, # Used to enable or disable the Swagger & ReDoc documentation,
    validate_security_config,  #function from app/core/config.py used to validate the security config for the application
)
from app.core.graph import compile_graph # function from app/core/graph.py used to compile the LangGraph application, Compiles the graph definition into a runnable graph instance, Builds your AI pipeline graph
from app.core.logging import setup_logging # function from app/core/logging.py used to set up the logging for the application, Configures logging for the application
from app.core.redis import init_redis, close_redis # functions from app/core/redis.py used to initialize and close the Redis connection for the application

# Initialize Logging
setup_logging()  #Console logging (for Docker/cloud), File logging (for local debugging), Log rotation (production safety), Noise filtering (clean logs), Consistent formatting
validate_security_config()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle management for the Collaborative RAG system."""
    # 1. Initialize storage backends before the first request arrives.
    init_cache_db()
    init_user_db()
    await init_redis() # connect to Redis
    
    # Initialize task queue pool
    app.state.arq_pool = await create_pool(redis_settings)
    
    # 2. Compile the graph once and attach a checkpoint backend. We keep SQLite
    # here for compatibility, but the location is now environment-configurable.
    # Postgres uses a connection pool.
    pool = AsyncConnectionPool(
        conninfo=CHECKPOINTS_DB_URL.replace("postgresql://", "postgres://"),
        max_size=20,
        kwargs={"autocommit": True}
    )
    await pool.open()

    memory = AsyncPostgresSaver(pool)
    await memory.setup()
    app.state.graph = compile_graph(memory)
    app.state.checkpointer = memory
    app.state.pg_pool = pool
    try:
        yield
    finally:
        await pool.close()
        await close_redis()
    
    print("Shutting down Research Intelligence Platform...")

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Research Intelligence Platform",
    description="Production-Grade Collaborative RAG with JWT Multi-Tenancy & Hybrid Search.",
    version="1.1.2",
    lifespan=lifespan,
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)

# --- CORS MIDDLEWARE ---
app.add_middleware(
    CORSMiddleware,
    # Restrict browser callers to known frontend origins instead of accepting
    # cross-origin credentials from every site on the internet.
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=TRUSTED_HOSTS or ["localhost", "127.0.0.1"],
)


@app.middleware("http")
async def add_security_headers(request, call_next):
    """Apply baseline response headers that reduce common browser-side attacks."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    # HSTS should only be sent by deployments that are actually behind HTTPS.
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# --- MIDDLEWARE & ASSETS ---
os.makedirs(ASSETS_DIR, exist_ok=True)
static_abs_path = os.path.abspath("assets")
app.mount("/assets", StaticFiles(directory=static_abs_path), name="assets")

# --- ROUTERS ---

app.include_router(auth_router, prefix="/api/v1/auth", tags=["Security"])
app.include_router(api_router, prefix="/api/v1", tags=["Research Engine"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
