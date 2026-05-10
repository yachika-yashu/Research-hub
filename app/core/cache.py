import json # Used to work with JSON data (used to store and retrieve data from the cache in JSON format)
import hashlib # Used to generate hash values (used to generate hash values for the cache keys)
from typing import Optional # Used to define optional types, In short it tells the function that it can return None
from qdrant_client.http import models as rest # Used to work with Qdrant models, Qdrant is a vector database, It is used to store and search for similar vectors, Aliased as 'rest' for cleaner usage later
from app.core.config import (
    GENERATION_MODEL, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, REDIS_EXACT_CACHE_TTL_SECONDS
)
from app.core.globals import openai_client # Used to access the centralized OpenAI api key
from app.core.qdrant import get_qdrant_client # This returns a shared Qdrant client instance that is used throughout the application to interact with the Qdrant vector database. # This is used to get the qdrant client instance
from app.core.redis import get_redis_client # This returns a shared Redis client instance that is used throughout the application to interact with the Redis in-memory data store. # This is used to get the redis client instance

# this is where two layer catching system is handled  1. exact cache(redis)  2. semantic cache(vector DB Qdrant)

CACHE_COLLECTION = "researhub_semantic_cache" #A collection in a vector database is similar to a table, but it’s optimized for vector similarity search rather than relational queries.
SIMILARITY_THRESHOLD = 0.95 # If the similarity score is greater than or equal to 0.95, it is considered a cache hit. VERY strict match, it prevents wrong answers for “almost similar” queries
EXACT_CACHE_PREFIX = "researhub:query" #A naming convention using prefixes that organizes keys, prevents conflicts, and enables grouped operations.
# In this case, 'query' is the feature. If you want to add more features later (like tenant_id, model_name, etc.),
# you can include them in the prefix or payload to make the cache keys more specific and prevent collisions.

def get_qdrant(): # get the qdrant client instance to use for later
    return get_qdrant_client()


def _build_exact_cache_key(tenant_id: str, query: str) -> str: # build the exact cache key
    """
    Queries are long so they become bad Redis keys, so here we are converting queries into fixed length keys we use a stable hash.
    Also the tenant prefix keeps cache hits isolated across teams.
    """  
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest() # Encodes the query string into bytes, creates SHA-256 hash, returns hexadecimal string
    return f"{EXACT_CACHE_PREFIX}:{tenant_id}:{query_hash}" #Final key looks like: researhub:query:team123:abc123hash

def init_cache_db(): # This runs at startup in main.py, this is used to initialize the semantic cache collection and create payload index
    """Initializes the semantic cache collection."""
    client = get_qdrant()
    collections = client.get_collections().collections
    exists = any(c.name == CACHE_COLLECTION for c in collections) #Checks if collection already exists
    
    if not exists:
        client.create_collection(
            collection_name=CACHE_COLLECTION,
            vectors_config=rest.VectorParams(
                size=EMBEDDING_DIMENSIONS, 
                distance=rest.Distance.COSINE
            )
        )
        client.create_payload_index(CACHE_COLLECTION, "tenant_id", rest.PayloadSchemaType.KEYWORD)
        print(f"CACHE: Collection '{CACHE_COLLECTION}' initialized.")


async def exact_cache_get(tenant_id: str, query: str) -> Optional[dict]:
    """Fast exact-match cache backed by Redis for repeated identical prompts."""
    client = get_redis_client()
    cache_key = _build_exact_cache_key(tenant_id, query)
    try:
        cached = await client.get(cache_key)
    except Exception as exc:
        print(f"CACHE: Redis exact-cache read bypassed due to error: {exc}")
        return None

    if not cached:
        return None

    print("CACHE: Exact Redis hit.")
    return json.loads(cached)


async def exact_cache_set(tenant_id: str, query: str, response: dict) -> None:
    """Store exact query responses in Redis with TTL to prevent stale indefinite reuse."""
    client = get_redis_client()
    cache_key = _build_exact_cache_key(tenant_id, query)
    try:
        await client.set(cache_key, json.dumps(response), ex=REDIS_EXACT_CACHE_TTL_SECONDS)
        print(f"CACHE: Exact Redis storage complete for '{query[:30]}...'.")
    except Exception as exc:
        print(f"CACHE: Redis exact-cache write bypassed due to error: {exc}")


async def invalidate_exact_cache_for_tenant(tenant_id: str) -> None:
    """
    Clear tenant-scoped exact cache after ingestion.

    Retrieval answers can become stale once the tenant vault changes, so we remove
    exact matches proactively. Semantic cache can be versioned later, but exact
    cache invalidation gives the biggest correctness win for the smallest change.
    """
    client = get_redis_client()
    pattern = f"{EXACT_CACHE_PREFIX}:{tenant_id}:*"
    try:
        keys = [key async for key in client.scan_iter(match=pattern)]
        if keys:
            await client.delete(*keys)
            print(f"CACHE: Invalidated {len(keys)} exact-cache entries for tenant {tenant_id}.")
    except Exception as exc:
        print(f"CACHE: Redis exact-cache invalidation bypassed due to error: {exc}")

async def semantic_cache_get(tenant_id: str, query: str) -> Optional[dict]:
    """Retrieves cached research responses using semantic similarity."""
    client = get_qdrant()
    
    # 1. Embed the query
    resp = await openai_client.embeddings.create(input=[query], model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIMENSIONS) #
    vector = resp.data[0].embedding
    
    # 2. Search for similar queries using the modern query_points API (Step 14 Fix)
    results = client.query_points(
        collection_name=CACHE_COLLECTION,
        query=vector,
        query_filter=rest.Filter(
            must=[rest.FieldCondition(key="tenant_id", match=rest.MatchValue(value=tenant_id))]
        ),
        limit=1
    ).points
    
    if results and results[0].score >= SIMILARITY_THRESHOLD:
        print(f"CACHE: Semantic Hit (Score: {results[0].score:.4f})")
        return json.loads(results[0].payload["response_json"])
    
    return None

async def semantic_cache_set(tenant_id: str, query: str, response: dict):
    """Caches successful research responses with embeddings."""
    client = get_qdrant()
    
    # 1. Embed the query
    resp = await openai_client.embeddings.create(input=[query], model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIMENSIONS)
    vector = resp.data[0].embedding
    
    # 2. Store in Qdrant
    import uuid
    point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{tenant_id}_{query}"))
    
    client.upsert(
        collection_name=CACHE_COLLECTION,
        points=[
            rest.PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "query": query,
                    "tenant_id": tenant_id,
                    "response_json": json.dumps(response),
                    "model": GENERATION_MODEL
                }
            )
        ]
    )
    print(f"CACHE: Semantic storage complete for '{query[:30]}...'")
