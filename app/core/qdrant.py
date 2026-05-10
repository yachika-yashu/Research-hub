import os
from typing import Optional
from qdrant_client import QdrantClient #qdrant is a vector database used to store and search for vectors. It stores vectors in a way that allows for fast similarity searches..QdrantClient() = a way to connect to the qdrant vector database.
from app.core.config import QDRANT_URL #used to get the URL of the qdrant vector database.

_qdrant_client: Optional[QdrantClient] = None

# This function is used to create single shared qdrant vector database client(object). It is used throughout the application to perform vector search and other qdrant operations.
# It uses the QDRANT_URL environment variable to determine the address of the qdrant server.
# if _qdrant_client is None (first time it is called) then it creates a new QdrantClient and stores it in _qdrant_client.
# Otherwise it returns the existing _qdrant_client.
#  This ensures that only one instance of the Qdrant client is created and used throughout the application, which is a common pattern for resource
#  management in Python applications. This approach is also known as "singleton pattern", although it's not a strict implementation of the singleton # pattern, it achieves a similar goal of having a single instance of a resource.

def get_qdrant_client() -> QdrantClient:
    """Return a shared Qdrant client using the standard network service endpoint."""
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(url=QDRANT_URL) # The Qdrant client uses the QDRANT_URL environment variable to determine the address of the qdrant server.
    return _qdrant_client
