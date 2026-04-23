# 07 Governance And Observability Flow

## Why this functionality exists

This app spends tokens, uses paid APIs, and makes research claims. That creates three product needs:

- cost visibility
- traceability
- operational debugging

That is why governance and observability are built into the request flow.

## Design thinking in order

### Step 1: Count cost and token usage

Every query and ingest should be attributable.

### Step 2: Preserve enough trace data to debug answers

Users and operators should be able to inspect what happened.

### Step 3: Log operational events

The system should emit logs to both local files and stdout.

## Dependencies introduced

- `sqlalchemy`
- Python `logging`
- `tiktoken`

## Files involved

- `app/core/database.py`
- `app/core/logic.py`
- `app/core/logging.py`
- `app/api/routes.py`

## Runtime execution flow

### Query governance

After query streaming finishes:

- `finalize_query_governance()` runs in the background

This function:

1. opens a DB session
2. counts input tokens
3. counts output tokens
4. estimates model cost
5. writes `UsageLog`
6. writes `TraceLog`
7. commits
8. updates caches

### Ingest governance

During ingestion completion:

- embedding token volume is counted
- estimated embedding cost is computed
- one ingest `UsageLog` row is written

### Operational logging

At startup:

- `setup_logging()` configures console and rotating file handlers

At runtime:

- routes log query and ingest events
- extractor logs OCR and Docling fallback events
- Redis layer logs connectivity behavior

## Flow across files

`main.py`
-> `app/core/logging.py`
-> route handlers in `app/api/routes.py`
-> token and cost helpers in `app/core/logic.py`
-> ORM writes through `app/core/database.py`

## What to rebuild first if doing this from scratch

1. Add application logging.
2. Add token counting.
3. Add cost estimation.
4. Add usage log table.
5. Add trace log table.
6. Add background governance on query completion.

That order keeps the system observable from early stages without forcing full analytics up front.
