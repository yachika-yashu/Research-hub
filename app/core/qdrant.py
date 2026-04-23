import os
from typing import Optional
from qdrant_client import QdrantClient
from app.core.config import QDRANT_URL

_qdrant_client: Optional[QdrantClient] = None

def get_qdrant_client() -> QdrantClient:
    """Return a shared Qdrant client using the standard network service endpoint."""
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(url=QDRANT_URL)
    return _qdrant_client
