import os
import uuid
from datetime import datetime
from sqlalchemy import create_engine, Column, String, DateTime, ForeignKey, Integer, Float, Text, Boolean
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

class Note(Base):
    """Researcher's personal notes with Markdown content."""
    __tablename__ = "notes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, index=True, nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False, default="Untitled Note")
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class PaperSummary(Base):
    """Auto-generated 5-sentence summary stored after ingestion."""
    __tablename__ = "paper_summaries"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, index=True, nullable=False)
    filename = Column(String, index=True, nullable=False)
    summary = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class PaperDetails(Base):
    """Canonical per-paper record — populated at ingest, queried by BibTeX export,
    structured extraction UI, and knowledge graph. Avoids repeated Qdrant scrolls
    for metadata that never changes after ingestion."""
    __tablename__ = "paper_details"

    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id   = Column(String, index=True, nullable=False)
    filename    = Column(String, index=True, nullable=False)
    title       = Column(String)
    authors     = Column(Text)          # JSON array string
    year        = Column(Integer, nullable=True)
    doi         = Column(String, nullable=True)
    journal     = Column(String, nullable=True)
    keywords    = Column(Text)          # JSON array string
    bibtex_key  = Column(String, nullable=True)
    # Structured extraction fields (populated after ingest LLM call)
    contribution = Column(Text, nullable=True)
    dataset      = Column(Text, nullable=True)
    baselines    = Column(Text, nullable=True)  # JSON array string
    limitations  = Column(Text, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)


class PaperReference(Base):
    """One row per cited work extracted from a paper's bibliography.
    Enables the citation-chain feature: click a reference → ingest it."""
    __tablename__ = "paper_references"

    id              = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id       = Column(String, index=True, nullable=False)
    source_filename = Column(String, index=True, nullable=False)
    ref_title       = Column(String)
    ref_authors     = Column(Text, nullable=True)   # JSON array string
    ref_year        = Column(Integer, nullable=True)
    ref_doi         = Column(String, nullable=True)
    arxiv_id        = Column(String, nullable=True) # inferred so user can one-click ingest
    extracted_at    = Column(DateTime, default=datetime.utcnow)


class ReadingQueueItem(Base):
    """Papers the researcher wants to read but hasn't indexed yet.
    Keeps the vault clean — only truly read papers enter the search index."""
    __tablename__ = "reading_queue"

    id        = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, index=True, nullable=False)
    user_id   = Column(String, ForeignKey("users.id"), nullable=False)
    title     = Column(String, nullable=False)
    authors   = Column(Text, nullable=True)   # JSON array string
    arxiv_id  = Column(String, nullable=True)
    url       = Column(String, nullable=True)
    notes     = Column(Text, default="")
    # "queued" | "reading" | "done"
    status    = Column(String, default="queued", index=True)
    added_at  = Column(DateTime, default=datetime.utcnow)


class ArxivMonitor(Base):
    """A keyword watch that the cron job uses to poll Arxiv daily.
    The worker calls export.arxiv.org/api/query from inside Docker —
    no public URL needed; this is outbound polling, not a webhook."""
    __tablename__ = "arxiv_monitors"

    id             = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id      = Column(String, index=True, nullable=False)
    user_id        = Column(String, ForeignKey("users.id"), nullable=False)
    keywords       = Column(Text, nullable=False)
    last_checked_at = Column(DateTime, nullable=True)
    is_active      = Column(Boolean, default=True)
    created_at     = Column(DateTime, default=datetime.utcnow)


class ArxivAlert(Base):
    """A single new paper discovered by the monitor job.
    Shown in the sidebar digest; user can dismiss or add to vault."""
    __tablename__ = "arxiv_alerts"

    id           = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    monitor_id   = Column(String, ForeignKey("arxiv_monitors.id"), index=True)
    tenant_id    = Column(String, index=True, nullable=False)
    arxiv_id     = Column(String, nullable=False)
    title        = Column(String)
    abstract     = Column(Text)
    authors      = Column(Text, nullable=True)   # JSON array string
    published_at = Column(String, nullable=True)
    is_read      = Column(Boolean, default=False)
    created_at   = Column(DateTime, default=datetime.utcnow)


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
