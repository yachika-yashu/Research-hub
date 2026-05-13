import os
from dotenv import load_dotenv
import tiktoken

load_dotenv()


def _parse_csv_env(name: str, default: str = "") -> list[str]:
    """Parse comma-separated env vars into a trimmed list."""
    raw_value = os.getenv(name, default)
    return [item.strip() for item in raw_value.split(",") if item.strip()]


APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV in {"production", "prod"}

# =============================================================================
# INFRASTRUCTURE SETTINGS
# =============================================================================
ASSETS_DIR = "assets/images" # used in main.py

# We standardize on the same networked services in local and production
# environments so the app behavior stays consistent across the lifecycle.
# Local development should run Qdrant on localhost; containers override this
# with the in-network service hostname.
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
# URL the user's browser uses to reach the API — needed for image src attributes in responses
API_PUBLIC_URL = os.getenv("API_PUBLIC_URL", "http://localhost:8000") #used to get the URL of the qdrant vector database.if the environment variable exists (in docker-composefile or when deployed), use it; otherwise, default to localhost:6333 (Qdrant is a vector database used to store and search for vectors)
QDRANT_COLLECTION = "research_platform"

# Named Vectors for Hybrid Search
VECTOR_NAME_DENSE = "dense-text"
VECTOR_NAME_SPARSE = "sparse-text"

# =============================================================================
# OCR & EXTRACTION SETTINGS
# =============================================================================
TESSERACT_CMD = os.getenv("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe" if os.name == "nt" else "/usr/bin/tesseract")
OCR_THRESHOLD = 50  # If docling gets < 50 chars, trigger OCR

# =============================================================================
# CLEANING & CHUNKING SETTINGS
# =============================================================================
REPEATED_LINE_THRESHOLD = 0.5
MIN_HEADER_LINE_LENGTH = 10

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200
TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")

# =============================================================================
# CACHE & DATABASE SETTINGS
# =============================================================================
# Use the same relational backend shape in local and production: PostgreSQL.
# Local development should point at localhost; containers override the hostname.
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://researhub:change_me_local@localhost:5432/researhub")
CHECKPOINTS_DB_URL = os.getenv("CHECKPOINTS_DB_URL", "checkpoints.db") # used in main.py

# Redis is used as a low-latency exact cache. Semantic cache remains in Qdrant.
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0") #If REDIS_URL exists in .env file → use itOtherwise → default to localhost
REDIS_EXACT_CACHE_TTL_SECONDS = int(os.getenv("REDIS_EXACT_CACHE_TTL_SECONDS", "3600")) #reuse answers for identical questions for 1 hour, then recompute to stay fresh, if set in .env file → use it Otherwise → default to 3600 (1 hour)

# =============================================================================
# SECURITY SETTINGS
# =============================================================================
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

# Local development stays convenient, but production must explicitly list origins.
ALLOWED_ORIGINS = _parse_csv_env(
    "ALLOWED_ORIGINS",
    "http://localhost:8501,http://127.0.0.1:8501,http://localhost:8000,http://127.0.0.1:8000",# used in main.py, update in production
)
TRUSTED_HOSTS = _parse_csv_env("TRUSTED_HOSTS", "localhost,127.0.0.1") # used in main.py, update in production
ENABLE_DOCS = os.getenv("ENABLE_DOCS", "true").strip().lower() == "true" # used in main.py, update in production
GOOGLE_OAUTH_ALLOW_INSECURE_HTTP = os.getenv(
    "GOOGLE_OAUTH_ALLOW_INSECURE_HTTP",
    "false" if IS_PRODUCTION else "true",
).strip().lower() == "true"

# =============================================================================
# AI MODEL SETTINGS
# =============================================================================

EMBEDDING_MODEL = "text-embedding-3-small" #produces a 1536-dimensional vector (full representation).
EMBEDDING_DIMENSIONS = 768  # Matryoshka dimensions, a way to reduce the size of the vector (for 1536-dimensional vector).(saved only 768 dimensions out of 1536 to save space, faster vector search, less cache misses ) #With 768: vectors form tighter semantic clusters, ANN search (HNSW in Qdrant) performs more reliably, So cache layer behaves like:“group similar questions together better”
EMBEDDING_BATCH_SIZE = 100

GENERATION_MODEL = "gpt-4o-mini"
GENERATION_TEMP = 0.1  # Low temperature for factual grounding

ENABLE_RERANKING = True
RERANKER_MODEL = "BAAI/bge-reranker-base"  # High-precision Cross-Encoder
RERANK_TOP_K = 25  # Candidates to consider for reranking

#This function is a startup security guardrail. Its purpose is to prevent the application from running if critical security configurations are unsafe, especially in production environments. It enforces safe defaults by validating key settings at startup and failing fast if misconfigurations are detected. This helps prevent security issues such as weak secret keys, overly permissive CORS settings, or insecure OAuth configurations from reaching a live system. By stopping the application during startup, it ensures that insecure defaults cannot silently reach production or expose the system to potential vulnerabilities.
def validate_security_config() -> None: # used in main.py, update in production
    """
    Fail fast on insecure production settings. If configuration is unsafe, crash immediately at startup instead of running insecurely.
    Security regressions are much cheaper to catch at startup than after the
    app has accepted traffic with weak defaults.
    """
    if not JWT_SECRET_KEY:
        raise RuntimeError("JWT_SECRET_KEY must be set before the application starts.")

    insecure_secret_markers = {
        "replace_with_a_long_random_secret",
        "change_me_local",
        "your_jwt_secret_here",
    }
    if IS_PRODUCTION and JWT_SECRET_KEY in insecure_secret_markers:
        raise RuntimeError("JWT_SECRET_KEY is using a placeholder value in production.")

    if IS_PRODUCTION and ("*" in ALLOWED_ORIGINS or not ALLOWED_ORIGINS):
        raise RuntimeError("ALLOWED_ORIGINS must be explicitly configured in production.") #Forces strict CORS policy preventing security risks like unauthorized domain access to your backend.

    if IS_PRODUCTION and GOOGLE_OAUTH_ALLOW_INSECURE_HTTP:
        raise RuntimeError("GOOGLE_OAUTH_ALLOW_INSECURE_HTTP must be false in production.") #Ensures that OAuth authentication always uses secure HTTPS connections.
