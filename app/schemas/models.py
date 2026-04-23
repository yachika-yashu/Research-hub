from datetime import datetime
from typing import List, Optional, Dict
from pydantic import BaseModel, Field, field_validator
from dataclasses import dataclass, field

# =============================================================================
# DATA OBJECTS (INTERNAL)
# =============================================================================

@dataclass
class DocumentChunk:
    """The atomic unit of the RAG pipeline."""
    chunk_id: str
    document_id: str
    tenant_id: str
    text: str
    chunk_type: str          # "text" | "table" | "figure"
    chunk_index: int
    char_start: int
    char_end: int
    token_count: int
    embedding: Optional[List[float]] = None
    media_url: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "chunk_id":    self.chunk_id,
            "document_id": self.document_id,
            "tenant_id":   self.tenant_id,
            "text":        self.text,
            "chunk_type":  self.chunk_type,
            "chunk_index": self.chunk_index,
            "char_start":  self.char_start,
            "char_end":    self.char_end,
            "token_count": self.token_count,
            "media_url":   self.media_url,
            "metadata":    self.metadata
        }

# =============================================================================
# SEMANTIC MODELS (STEP 25)
# =============================================================================

class PaperMetadata(BaseModel):
    """Semantic metadata extracted from a research paper."""
    title: Optional[str] = None
    authors: List[str] = []
    year: Optional[int] = None
    doi: Optional[str] = None
    journal: Optional[str] = None
    keywords: List[str] = []

    @field_validator("authors", "keywords", mode="before")
    @classmethod
    def ensure_list(cls, v):
        return v if isinstance(v, list) else []

# =============================================================================
# API MODELS (FASTAPI)
# =============================================================================

class QueryFilters(BaseModel):
    """Logical filters for research queries."""
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    authors: Optional[List[str]] = None
    journal: Optional[str] = None
    chunk_type: Optional[str] = None
    filename: Optional[str] = None

class CleaningMetadata(BaseModel):
    original_char_count: int
    cleaned_char_count: int
    chars_removed: int
    ligatures_fixed: int
    hyphenations_fixed: int
    repeated_lines_removed: int
    control_chars_removed: int

class ChunkingStats(BaseModel):
    total_chunks: int
    text_chunks: int
    table_chunks: int
    figure_chunks: int
    total_tokens: int
    estimated_embedding_cost_usd: float

class IngestResponse(BaseModel):
    filename: str
    status: str
    tenant_id: str
    extraction_method: str
    message: str
    metadata: Optional[PaperMetadata] = None
    cleaning: CleaningMetadata
    chunking: ChunkingStats

class QueryRequest(BaseModel):
    query: str
    tenant_id: str = "default"
    thread_id: Optional[str] = None # Added for Step 51: Session Persistence
    limit: int = 5
    filters: Optional[QueryFilters] = None

class Citation(BaseModel):
    chunk_id: str
    text: str
    score: float
    precision_score: Optional[float] = None
    media_url: Optional[str] = None
    metadata: dict = {}

class QueryResponse(BaseModel):
    answer: str
    citations: List[Citation]
    status: str = "success"
    faithfulness_score: Optional[float] = None  # Step 46
    faithfulness_reasoning: Optional[str] = None # Step 46
    timestamp: datetime = Field(default_factory=datetime.now)
