# 02 Retrieval And Indexing Flow

## Why this functionality exists

The system is only useful if the agent can retrieve the right chunk from the vault with high precision.

A naive semantic-only search is not enough for research questions because users ask for:

- exact terminology
- figure references
- metadata-constrained queries
- very specific paper details

That is why retrieval combines:

- dense semantic vectors
- sparse keyword vectors
- metadata filters
- reranking

## Design thinking in order

### Step 1: Decide that the vault is chunk-based, not document-based

The agent needs fine-grained citations, so retrieval must operate over chunks rather than whole documents.

### Step 2: Support both semantic meaning and exact term matching

Dense vectors help with meaning.

Sparse BM25 helps with exact terms and scientific vocabulary.

That led to hybrid retrieval.

### Step 3: Support logical constraints

Users ask for papers by:

- year
- author
- journal
- filename
- chunk type

That drove the metadata indexing plan in Qdrant.

### Step 4: Improve the top-K ranking

Initial retrieval can still contain noisy candidates, so reranking was added as the final precision stage.

## Dependencies introduced for retrieval

- `qdrant-client`
- `fastembed`
- `openai`
- `BAAI/bge-reranker-base` through `fastembed.rerank.cross_encoder`

## Files involved

- `app/services/vector_store.py`
- `app/core/qdrant.py`
- `app/core/globals.py`
- `app/core/config.py`
- `app/services/tools.py`
- `app/schemas/models.py`

## Storage initialization flow

### 1. Startup calls `init_db()`

`main.py -> app/services/vector_store.py -> init_db()`

This function:

- checks whether the Qdrant collection exists
- checks whether it is the expected hybrid shape
- deletes legacy non-hybrid collection if necessary
- creates the collection with dense and sparse vector configs
- creates payload indexes

This is not just setup boilerplate. It encodes the retrieval design contract.

## Query-time retrieval flow

### 1. Tool layer calls `search_vdb()`

The main retrieval path is usually entered from:

- `app/services/tools.py -> rag_tool()`

That tool normalizes the retrieval intent and then calls:

- `app/services/vector_store.py -> search_vdb()`

### 2. Filters are built

`search_vdb()` constructs a Qdrant filter starting with:

- `tenant_id`

Then it optionally adds:

- year range
- author match
- journal match
- chunk type
- filename

This is where multi-tenancy and logical retrieval constraints become storage-level behavior.

### 3. Dense embedding is computed

The query text is embedded with OpenAI.

This produces the dense semantic vector.

### 4. Sparse embedding is computed

The same query is passed through the BM25 sparse encoder.

This produces the sparse keyword vector.

### 5. Hybrid fusion query is executed

Qdrant receives:

- one dense prefetch
- one sparse prefetch
- one fusion query using RRF

This is the core hybrid retrieval step.

### 6. Fallback search may run

If hybrid retrieval returns no results:

- the system falls back to dense-only semantic search

This protects the user experience from sparse-stream edge cases.

### 7. Reranking runs

If reranking is enabled:

- passages are sent through the cross-encoder reranker
- `precision_score` is attached to each hit
- hits are resorted by that score

### 8. Result payloads are returned

The final result list contains payload dictionaries, not raw Qdrant objects.

That keeps the tool layer and route layer simpler.

## Indexing flow from ingestion into retrieval

Indexing and retrieval are two halves of the same design.

The ingestion side writes points with:

- `tenant_id`
- `metadata.year`
- `metadata.authors`
- `metadata.journal`
- `chunk_type`
- `metadata.ingested_at`

The retrieval side depends on those fields existing and being indexed.

That is why indexing and retrieval should be understood together, not as separate concerns.

## Flow across files

Retrieval-time file trace:

`app/services/tools.py: rag_tool`
-> `app/services/vector_store.py: search_vdb`
-> `app/core/globals.py: openai_client`
-> `app/services/vector_store.py: get_sparse_encoder`
-> `app/core/qdrant.py: get_qdrant_client`

Indexing-time file trace:

`app/services/ingestion.py`
-> `app/core/logic.py: embed_chunks`
-> `app/services/vector_store.py: get_sparse_encoder`
-> `app/core/qdrant.py: get_qdrant_client`

## What to rebuild first if doing this from scratch

1. Create Qdrant collection with dense vectors only.
2. Add sparse vectors.
3. Add tenant filter.
4. Add metadata indexes.
5. Add reranker.
6. Add fallback search path.

That lets you validate the retrieval value incrementally.
