# 16 Interview Prep

## 1. Why is this system built on FastAPI instead of Flask?

Strong answer:

FastAPI matches the workload better because the system streams SSE responses, performs async provider calls, and relies on typed request models. Flask could serve the routes, but the async and validation ergonomics would be worse, especially for long-lived query streams.

Follow-up:

Async does not solve CPU-heavy OCR or parsing, so those still need worker separation for scale.

## 2. Why use LangGraph instead of a simple LangChain chain?

Strong answer:

The runtime needs a cyclic control loop. The model may call tools, inspect results, then call another tool before answering. LangGraph models that as explicit nodes and edges with checkpointed state, which is more robust than a single linear chain.

Follow-up:

The current graph is minimal: `agent -> tools -> agent`.

## 3. How does tenant isolation work?

Strong answer:

Users are associated with a `team_code`, which is hashed into `tenant_id`. Retrieval and cache queries always filter by `tenant_id`, so the vector database enforces isolation at query time rather than trusting the LLM prompt alone.

Follow-up:

That is stronger than prompt-only isolation because storage-level filters remain authoritative.

## 4. Where is conversational state stored?

Strong answer:

Two places. The UI keeps current display state in Streamlit session state, and LangGraph persists thread state in `checkpoints.db` using `thread_id`. That lets the backend reconstruct chat history across requests.

Follow-up:

This is durable only on the local node today.

## 5. How does the query request flow end to end?

Strong answer:

Streamlit sends JSON to FastAPI over SSE. FastAPI authenticates the user, checks semantic cache, then runs LangGraph. The graph may call tools such as vault retrieval. Tokens and tool events stream back to the UI. After completion, the backend logs usage and trace data and stores the response in semantic cache.

## 6. Why is SSE used instead of waiting for a final JSON response?

Strong answer:

The user experience is much better when first-token latency is visible and tool progress is surfaced. SSE is simpler than WebSockets for one-way server-to-client token streaming and fits this workload well.

## 7. How does retrieval work internally?

Strong answer:

The system generates dense embeddings with OpenAI and sparse BM25 vectors with `fastembed`, runs hybrid fusion in Qdrant with tenant filters, then optionally reranks the result set with a cross-encoder before formatting context for the model.

## 8. What is the semantic cache and why is it unusual?

Strong answer:

Instead of exact-key caching in Redis, the code stores prior answers in a Qdrant collection and looks them up by embedding similarity. That allows paraphrased repeat questions to hit cache, but lookup still pays embedding cost and there is no TTL yet.

## 9. What are the main production weaknesses of the current design?

Strong answer:

The biggest issues are local SQLite for durable state, local disk for checkpoints and images, and CPU-heavy ingestion inside API workers. Those choices are fine locally but block clean horizontal scaling.

## 10. Why is Qdrant a good fit here?

Strong answer:

Research queries benefit from hybrid dense+sparse retrieval and metadata filters like year, journal, and tenant. Qdrant supports those well and also works locally, which matches the current development model.

## 11. What happens if the LLM provider is slow or unavailable?

Strong answer:

Answer generation, embeddings, and metadata extraction all depend on OpenAI, so provider issues affect both query and ingest. Cache failures degrade better than generation failures because cache lookup is treated as optional optimization.

## 12. Why are background tasks used after streaming the answer?

Strong answer:

Usage logging and cache writes are moved off the critical path so the user sees the answer sooner. The tradeoff is that governance writes become best-effort after response delivery instead of strongly coupled to it.

## 13. How would you migrate this to PostgreSQL and Redis?

Strong answer:

Move user, usage, and trace tables to PostgreSQL with proper indexes and pooling. Use Redis for exact cache and ephemeral coordination, not for durable audit data. Keep Qdrant for retrieval. Also move graph checkpointing to a shared backend if multiple API replicas are introduced.

## 14. Why is the ingestion pipeline structured in stages?

Strong answer:

It separates extraction, cleaning, chunking, embedding, sparse encoding, and persistence so each phase can expose progress, fail independently, and be optimized or offloaded later without rewriting the full flow.

## 15. If you rebuilt this from scratch for production, what would you keep and what would you change?

Strong answer:

I would keep FastAPI, LangGraph, and Qdrant because they fit the interaction and retrieval model. I would replace SQLite with PostgreSQL, add Redis for ephemeral shared state, move ingestion to workers, move images to object storage, and use a shared checkpoint backend so API replicas can serve the same chat threads.
