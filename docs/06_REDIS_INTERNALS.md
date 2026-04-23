# 06 Redis Internals

## Current role

Redis is now used as the fast exact-match query cache in all environments.

Implementation points:

- client lifecycle in `app/core/redis.py`
- exact cache helpers in `app/core/cache.py`
- query-path integration in `app/api/routes.py`
- ingestion-driven invalidation in `app/services/ingestion.py`

## What role Redis was probably intended to play

Given the architecture, Redis is the natural fit for three jobs:

1. low-latency cache
2. ephemeral session state
3. queue or broker support for async jobs

## How Redis would map to this project

### Exact cache

Real key pattern:

- `hanuman:query:{tenant_id}:{sha256(query)}`

Real command flow:

- `GET` during `/query`
- `SET ... EX <ttl>` after successful response generation
- `SCAN` + `DEL` after ingestion for the same tenant

Why Redis helps:

- lower latency than embedding-based semantic cache
- TTL support for stale answer control
- shared exact cache across API replicas

### Session or thread state

Best candidate:

- mapping `thread_id` to lightweight session metadata
- active stream status or inflight request coordination

Likely commands:

- `HSET thread:{thread_id} tenant_id ...`
- `EXPIRE thread:{thread_id} 86400`

### Queue

Best candidate:

- background paper ingestion
- retries for failed arXiv downloads
- long OCR jobs

Likely commands if implemented directly:

- `LPUSH ingest_jobs ...`
- `BRPOP ingest_jobs`

Or Redis could back Celery/RQ rather than being used directly.

## TTL behavior

Redis exact cache uses TTL through `REDIS_EXACT_CACHE_TTL_SECONDS`.

Current behavior:

- identical prompts can hit cache until TTL expires
- tenant cache is proactively cleared after ingestion
- TTL bounds stale answer lifetime even if invalidation is missed

Why TTL matters:

- avoids stale LLM outputs living forever
- prevents memory growth without manual cleanup
- lets the system distinguish ephemeral state from durable state

## Why Redis would be chosen

Redis would be a good fit because it is:

- extremely fast for ephemeral access patterns
- shared across workers and containers
- simple to expire
- well suited for cache and coordination state

## What happens if Redis fails

Redis is treated as an optimization dependency.

Behavior:

- startup logs a warning if `PING` fails
- query flow bypasses exact cache on read/write errors
- graph execution and semantic cache still work

So the system degrades to slower behavior rather than total failure.

The same design rule still applies:

- Redis should hold ephemeral optimization state
- PostgreSQL/SQLite should hold durable business state

## Recommendation

Next production step:

- keep Redis for exact cache
- optionally add queue semantics for ingestion workers
- do not make Redis the source of truth for auth, audits, or long-term chat history
