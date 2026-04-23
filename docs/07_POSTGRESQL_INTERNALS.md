# 07 PostgreSQL Internals

## Current role

PostgreSQL is the default relational backend for both local and production environments.

Current behavior:

- the app expects `DATABASE_URL` to point at PostgreSQL
- local development should run Postgres on `localhost`
- Docker and AWS deployments override only the hostname/credentials
- SQLAlchemy normalizes `postgres://` and `postgresql://` URLs to `postgresql+psycopg://`

## What the current relational layer does

The relational schema in `app/core/database.py` contains:

- `users`
- `usage_logs`
- `trace_logs`

Those tables support:

- authentication lookup
- multi-tenant user membership
- governance and cost accounting
- debugging traces

## Current query behavior

Examples of real query patterns:

- `db.query(User).filter(User.username == username).first()`
- `db.query(UsageLog).filter(UsageLog.tenant_id == tenant_id).all()`
- `db.query(TraceLog).filter(...).first()`

These are ORM-generated queries, not raw SQL cursors. They hit PostgreSQL in both local and production setups.

## Transactions

The current code uses SQLAlchemy sessions with explicit:

- `db.add(...)`
- `db.flush()`
- `db.commit()`
- `db.rollback()`

Why that matters:

- `UsageLog` and `TraceLog` are committed together in `finalize_query_governance()`
- if trace logging fails before commit, rollback prevents partial durable state for that unit of work

That is a real transactional boundary on PostgreSQL in both local and production setups.

## PostgreSQL production fit

PostgreSQL is the right production-grade replacement for `research.db` and probably for LangGraph checkpoints as well.

### Data storage fit

Strong fits:

- users and auth data
- usage and billing records
- trace logs and JSON columns
- checkpoint metadata if a PG saver is adopted

### Indexing strategy

Likely indexes:

- `users(username)` unique
- `users(team_code)`
- `users(tenant_id)`
- `usage_logs(tenant_id, timestamp desc)`
- `usage_logs(user_id, timestamp desc)`
- `trace_logs(usage_log_id)`
- `trace_logs(tenant_id)`

Why:

- auth lookup is by username
- dashboards are tenant-scoped and time-oriented
- trace retrieval joins naturally from usage event to trace record

### ACID behavior

PostgreSQL would improve:

- durability under concurrent writers
- isolation under multiple API processes
- transactional integrity beyond a single local file

Why ACID matters here:

- auth writes should not race inconsistently
- audit tables should not partially persist
- concurrent dashboard and query traffic should not lock the whole database file

## Connection pooling

### Today

The engine is built for PostgreSQL pooling with:

- `pool_pre_ping`
- `pool_size`
- `max_overflow`

Why pooling matters:

- each HTTP request should not open a brand new TCP+TLS+auth handshake
- pool exhaustion becomes a backpressure signal
- DB resource use becomes predictable

## Query execution differences from SQLite

Moving to PostgreSQL changes behavior in meaningful ways:

- concurrent writes improve substantially
- query planners become more sophisticated
- JSONB becomes viable for trace metrics
- advisory locks and richer transaction isolation become available

## Production recommendation

For serious production:

- point `DATABASE_URL` at PostgreSQL
- model `metrics_json` and `faithfulness_report_json` as `JSONB` in a future migration
- add tenant-and-time composite indexes
- move LangGraph checkpoints off local SQLite if multi-replica chat continuity matters
