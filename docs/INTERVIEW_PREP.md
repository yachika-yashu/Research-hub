# 🎤 Interview Preparation: Hanuman Project

If you are asked about this project in a technical interview, here are the high-level questions and how to answer them from a "Senior Engineer" perspective.

## 1. "How do you handle data privacy in a multi-tenant RAG system?"
*   **Key Phrase**: "Tenant Isolation at the Vector Level."
*   **Answer**: "We don't rely on the LLM to 'filter' data. Instead, we use Qdrant's metadata filtering. Every chunk is tagged with a `tenant_id`. When a user queries, we inject a mandatory filter: `where tenant_id == user.tenant_id`. The vector database only returns chunks the user is authorized to see."

## 2. "Why use Hybrid Search instead of just Semantic Search?"
*   **Key Phrase**: "Keyword Precision vs. Semantic Intent."
*   **Answer**: "Semantic search (Dense) is great for 'vibe' and meaning, but it fails on specific terminology (e.g., 'Model 2.5b-v2'). Sparse vectors (BM25 style) excel at exact keyword matching. By combining them, we get high recall (semantic) and high precision (keywords)."

## 3. "How do you solve the 'Hallucination' problem in your system?"
*   **Key Phrase**: "Factual Grounding & Tool Constraints."
*   **Answer**: "We use two strategies: First, we set the temperature low (0.1). Second, we use LangGraph to enforce a 'search-first' behavior. The agent is strictly instructed to only use information provided by the `rag_tool`. If no information is found, it's taught to say 'I don't know' rather than guessing."

## 4. "How do you handle long-running ingestion without timing out the browser?"
*   **Key Phrase**: "Server-Sent Events (SSE) & Async Background Tasks."
*   **Answer**: "Standard HTTP POST requests time out after ~30s. Document processing can take minutes. We use SSE to stream a live progress updates to the frontend. This keeps the connection alive and provides a better UX by showing the user exactly which stage the ingestion is in."

## 5. "Why did you choose Docling over standard PDF parsers?"
*   **Key Phrase**: "Layout-Aware Extraction."
*   **Answer**: "Standard parsers treat PDFs as a flat stream of characters. Docling uses vision models to understand the 'blocks' of the page. This allows us to correctly identify and extract tables and images, which are often the most valuable parts of a research paper."
