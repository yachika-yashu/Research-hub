import os
import uuid
from datetime import datetime
from sqlalchemy import create_engine, Column, String, DateTime, ForeignKey, Integer, Float, Text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import DATABASE_URL

# --- CONFIGURATION ---
# Normalize SQLAlchemy URLs so local and production can use the same
# PostgreSQL-backed code path with only environment differences.
DB_URL = DATABASE_URL
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DB_URL.startswith("postgresql://") and "+psycopg" not in DB_URL:
    DB_URL = DB_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine_kwargs = {}
# PostgreSQL connections should be pooled and pre-pinged so stale sockets are
# recycled instead of causing first-request failures after deploys or restarts.
engine_kwargs.update(
    pool_pre_ping=True,
    pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
)

engine = create_engine(DB_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- MODELS ---

class User(Base):
    """Production-grade user model with Team-based multi-tenancy."""
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    
    # Team-based Multi-Tenancy (Step 45)
    team_code = Column(String, index=True, nullable=False)
    tenant_id = Column(String, index=True, nullable=False) # Derived from team_code
    
    created_at = Column(DateTime, default=datetime.utcnow)

class UsageLog(Base):
    """Production observability model for tracking API consumption and metrics."""
    __tablename__ = "usage_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, index=True, nullable=False)
    user_id = Column(String, ForeignKey("users.id"))
    
    event_type = Column(String, index=True) # "ingest" | "query"
    model_name = Column(String)
    
    tokens_input = Column(Integer, default=0)
    tokens_output = Column(Integer, default=0)
    estimated_cost_usd = Column(Float, default=0.0)
    
    metrics_json = Column(Text) # JSON string for faithfulness, latency, etc.
    timestamp = Column(DateTime, default=datetime.utcnow)

class TraceLog(Base):
    """Deep observability model for debugging RAG pipeline internals."""
    __tablename__ = "trace_logs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    usage_log_id = Column(String, ForeignKey("usage_logs.id"), index=True)
    tenant_id = Column(String, index=True)
    
    full_prompt = Column(Text)
    context_data_json = Column(Text)
    faithfulness_report_json = Column(Text)

def init_db():
    """Initialize relational tables on the shared PostgreSQL backend."""
    Base.metadata.create_all(bind=engine)

def get_db():
    """Yield one SQLAlchemy session per request/background task unit of work."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
