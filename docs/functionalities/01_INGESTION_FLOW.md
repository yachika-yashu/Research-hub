# 01 Ingestion Flow

## Why this functionality exists

The product needs to take a research PDF and turn it into something an agent can search precisely later.

That means ingestion must do more than store the file. It must:

- extract usable text
- preserve tables and figures
- assign tenant ownership
- chunk the content for retrieval
- embed and index it
- expose progress to the UI

## Design thinking in order

### Step 1: Decide what the output of ingestion should be

The end product is not a PDF file record. The end product is a set of searchable chunks with metadata and optional media links.

That is why the design centers around `DocumentChunk` in `app/schemas/models.py`.

### Step 2: Choose an extractor that understands research documents

A normal text extractor is not enough for scientific PDFs because:

- tables matter
- figures matter
- OCR fallback is needed for scans

That is why `Docling` was chosen first, with OCR fallback through `pytesseract` and `pdf2image`.

### Step 3: Make extraction visible to the user

Ingestion is slow enough that the frontend needs streaming progress. That drove the decision to make `/ingest` an SSE endpoint instead of a plain blocking POST.

### Step 4: Decide where the searchable output lives

Searchable chunks need vector storage. That led to Qdrant as the final persistence target for chunk payloads and vectors.

## Dependencies introduced for ingestion

These are the packages that exist primarily because of ingestion.

- `docling`
- `pytesseract`
- `pdf2image`
- `Pillow`
- `langchain-text-splitters`
- `openai`
- `fastembed`
- `qdrant-client`

These are the system packages the Docker image installs for ingestion.

- `tesseract-ocr`
- `poppler-utils`

## Files involved

### Request entrypoint

- `app/api/routes.py`

### Pipeline controller

- `app/services/ingestion.py`

### Extraction engine

- `app/services/extractor.py`

### Cleaning, chunking, metadata, embeddings

- `app/core/logic.py`

### Storage layer

- `app/services/vector_store.py`
- `app/core/qdrant.py`
- `app/core/database.py`

### Data contracts

- `app/schemas/models.py`

## Runtime execution flow

### 1. Frontend sends PDF

The user uploads a PDF in `dashboard.py`.

`dashboard.py` calls:

- `POST /api/v1/ingest`

It opens the request as SSE because the backend will emit progress events.

### 2. FastAPI receives the file

`app/api/routes.py -> ingest_document()`

This route:

- authenticates the user
- reads file bytes
- calls `stream_process_ingestion()`
- wraps the async generator in `StreamingResponse`

### 3. Pipeline orchestration begins

`app/services/ingestion.py -> stream_process_ingestion()`

This function is the conductor of the whole ingestion flow.

It emits progress milestones and delegates work to the lower layers.

### 4. Extraction is executed

`stream_process_ingestion() -> extract_text()`

`app/services/extractor.py -> extract_text()`

Inside this function:

1. PDF bytes are read
2. a temporary `.pdf` file is created
3. `Docling` converts it
4. markdown is exported
5. image crops are saved under `assets/images`
6. an `image_map` is built
7. markdown image placeholders are replaced with stable markers

If the output is too short or Docling fails:

- OCR fallback runs through `ocr_extract()`

### 5. Metadata extraction runs

`stream_process_ingestion() -> extract_paper_metadata()`

`app/core/logic.py -> extract_paper_metadata()`

This calls OpenAI with a constrained JSON output prompt to extract:

- title
- authors
- year
- doi
- journal
- keywords

This step exists so later retrieval can filter by semantic metadata rather than only raw text.

### 6. Cleaning runs

`stream_process_ingestion() -> clean_text()`

`app/core/logic.py -> clean_text()`

This step:

- normalizes Unicode
- removes control characters
- fixes ligatures
- de-hyphenates line-break artifacts
- removes repeated noise lines

The goal is not cosmetic cleanup. The goal is to improve later chunk quality and embedding quality.

### 7. Chunking runs

`stream_process_ingestion() -> chunk_document()`

`app/core/logic.py -> chunk_document()`

This step:

- uses `RecursiveCharacterTextSplitter`
- creates chunk IDs
- computes token counts
- links markdown picture markers to saved image URLs
- labels chunks as `text`, `figure`, or `table`

### 8. Dense embeddings run

`stream_process_ingestion() -> embed_chunks()`

`app/core/logic.py -> embed_chunks()`

This sends chunk text to OpenAI embeddings and writes the vectors back onto the `DocumentChunk` objects.

### 9. Sparse encoding runs

`stream_process_ingestion() -> get_sparse_encoder()`

`app/services/vector_store.py -> get_sparse_encoder()`

This lazily initializes the BM25 sparse encoder and generates sparse vectors for the same chunk texts.

### 10. Qdrant persistence runs

Back in `app/services/ingestion.py`, the code builds `PointStruct` objects and calls:

- `qdrant.upsert(...)`

Each point contains:

- deterministic point ID
- dense vector
- sparse vector
- payload from `DocumentChunk.to_dict()`

### 11. Cache invalidation runs

Still in `stream_process_ingestion()`, after successful upsert:

- `invalidate_exact_cache_for_tenant()` is called

The reasoning is simple: once the corpus changes, previously exact-cached answers may be stale.

### 12. Usage logging runs

The function then writes an ingest `UsageLog` record to PostgreSQL through SQLAlchemy.

### 13. Result is streamed back

The generator emits:

- progress events
- one final `completed` event

The UI then reports the ingestion result.

## Flow across files

Use this as the shortest possible file trace.

`dashboard.py`
-> `app/api/routes.py: ingest_document`
-> `app/services/ingestion.py: stream_process_ingestion`
-> `app/services/extractor.py: extract_text`
-> `app/core/logic.py: extract_paper_metadata`
-> `app/core/logic.py: clean_text`
-> `app/core/logic.py: chunk_document`
-> `app/core/logic.py: embed_chunks`
-> `app/services/vector_store.py: get_sparse_encoder`
-> `app/core/qdrant.py: get_qdrant_client`
-> `app/core/cache.py: invalidate_exact_cache_for_tenant`
-> `app/core/database.py` models and session

## What to rebuild first if doing this from scratch

1. Get `extract_text()` working with one PDF.
2. Add `clean_text()`.
3. Add `chunk_document()`.
4. Add OpenAI embeddings.
5. Add Qdrant upsert.
6. Add metadata extraction.
7. Add SSE progress.
8. Add cache invalidation and usage logging.

That order follows the real dependency chain instead of forcing every production feature up front.
