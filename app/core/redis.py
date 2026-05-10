import logging # Used to print structured logs like INFO/WARNING instead of print().
from typing import Optional

import redis.asyncio as redis # This is the async version of Redis client.Allows await client.ping() instead of blocking calls.Important for FastAPI / async apps.

from app.core.config import REDIS_URL #for local development, docker-compose overrides .env

logger = logging.getLogger("ResearchHub.redis")

_redis_client: Optional[redis.Redis] = None # Stores the singleton async Redis client i.e. Only ONE Redis connection is created for the whole app

# Redis is used as an exact-match cache layer:
# identical requests can be served from cache instead of recomputing results.
# This improves response time and reduces expensive operations (e.g., LLM calls).

# It may also store request metadata (e.g., trace_id) for fast lookup,
# observability, and debugging, depending on implementation.


# Lazy Initialization Singleton:
# - Not created at import time
# - Created only on first use
# - Reused across the application
def get_redis_client() -> redis.Redis:
    """Return a shared async Redis client, creating it only if it doesn't already exist."""
    
    global _redis_client  # Allows modification of the global variable, not a local copy

    if _redis_client is None:
        # First call: initialize the Redis client
        _redis_client = redis.from_url(
            REDIS_URL,
            decode_responses=True  # Return strings instead of bytes (easier JSON handling)
        )

    return _redis_client  # Always return the shared instance

async def init_redis() -> bool:
    """Attempt to connect to Redis during startup (in main.py). If Redis is unavailable, we log a warning but continue startup—Redis is treated as an optimization rather than a hard dependency, allowing local dev and degraded-mode operation."""
    client = get_redis_client()
    try:
        await client.ping() # Sends a ping command to Redis server , if it fails then it raises an exception which is caught by the except block.
        logger.info("REDIS: Connected to exact-cache backend.")
        return True
    except Exception as exc:
        logger.warning("REDIS: Unavailable during startup, exact cache disabled. %s", exc) #logs a warning message , doesn't crash the app.
        return False

# graceful shutdown
async def close_redis() -> None:
    """Close the shared Redis connection cleanly on shutdown (in main.py)."""
    global _redis_client # use global variable for Redis client (singleton)
    if _redis_client is not None:
        await _redis_client.aclose() # Closes the connection pool
        _redis_client = None # Sets the client to None so it can be re-initialized if needed
