# 04 Redis Cache Flow

## Why this functionality exists

The agent path is expensive because it may involve:

- retrieval
- reranking
- model generation
- governance writes

If the exact same question is asked again, that full path should not run every time.

That is why Redis exact caching exists.

## Design thinking in order

### Step 1: Add the cheapest possible cache first

Semantic cache is useful, but it still requires embeddings and Qdrant search.

Redis exact cache is cheaper:

- one key lookup
- no embedding call
- no graph execution

### Step 2: Keep cache scoped by tenant

This app is multi-tenant. Cache hits must never cross tenant boundaries.

That led to tenant-prefixed key design.

### Step 3: Accept that cache is an optimization, not a dependency

If Redis is down, the app should still answer queries by falling back to the graph path.

That drove the fail-open behavior in `app/core/redis.py` and `app/core/cache.py`.

## Dependencies introduced

- `redis`

## Files involved

- `app/core/redis.py`
- `app/core/cache.py`
- `app/api/routes.py`
- `app/services/ingestion.py`
- `main.py`

## Runtime execution flow

### 1. Startup warms Redis

`main.py -> init_redis()`

`app/core/redis.py -> init_redis()`

This does a `PING` to verify connectivity, but does not block app startup if Redis is unavailable.

### 2. Query checks exact cache

`app/api/routes.py -> handle_query()`

This calls:

- `exact_cache_get(tenant_id, query)`

### 3. Key generation happens

`app/core/cache.py -> _build_exact_cache_key()`

The key format is:

- prefix
- tenant ID
- SHA-256 hash of query text

This avoids massive raw query strings as Redis keys.

### 4. Redis GET happens

`app/core/cache.py -> exact_cache_get()`

This uses the shared client from:

- `app/core/redis.py -> get_redis_client()`

### 5. Query short-circuits on hit

If Redis returns a value:

- FastAPI immediately streams that answer
- LangGraph is skipped

### 6. Redis SET happens after successful query

`app/api/routes.py -> finalize_query_governance()`

This calls:

- `exact_cache_set(...)`

The value is stored with TTL.

### 7. Cache invalidation happens after ingestion

When a new paper is added:

- exact cache for that tenant is cleared

The logic lives in:

- `app/services/ingestion.py`
- `app/core/cache.py -> invalidate_exact_cache_for_tenant()`

The reason is correctness: new documents may change the best answer for a repeated question.

## Flow across files

`main.py`
-> `app/core/redis.py`
-> `app/core/cache.py`
-> `app/api/routes.py`
-> `app/services/ingestion.py`

## What to rebuild first if doing this from scratch

1. Add Redis client wrapper.
2. Add exact `GET` on query path.
3. Add exact `SET` after successful query completion.
4. Add tenant-prefixed invalidation on ingestion.
5. Only then consider more advanced cache patterns.

This sequence gives most of the value with the least moving parts.
