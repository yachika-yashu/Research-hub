# 🧬 Hanuman: Multi-Tenant Agentic RAG Architecture

## 1. First Principles: Why This Project Exists

The fundamental problem Hanuman solves is **"The Academic Needle in the Multimodal Haystack."** 

Standard RAG (Retrieval-Augmented Generation) systems usually fail in research contexts because:
1. **Multimodality**: Papers convey 40% of their value through tables and figures, which most parsers ignore.
2. **Precision**: "Close enough" isn't good enough for science. We need specific citations.
3. **Collaboration**: Research is a team sport; data must be isolated by teams (multi-tenancy).

---

## 2. Core Architectural Flow

### A. Ingestion Pipeline (The "Multimodal Digest")
1. **Extraction (Docling 2.x)**: 
   - *Why*: We use Docling because it understands document layout (Object Detection) rather than just reading raw text.
   - *Logic*: It converts PDF -> Markdown, but importantly, it extracts images and tables as separate objects.
   - *Enhancement*: We "patch" the markdown with stable IDs (`<!-- picture-0 -->`) to link text to visuals.
2. **Cleaning & Chunking**:
   - *Logic*: We split text into ~1500 token chunks with overlap.
   - *Why*: Large enough to preserve context, small enough to fit in the LLM's "Reasoning Window."
3. **Indexing (Qdrant)**:
   - *Hybrid Search*: We save both Dense vectors (Semantic meaning) and Sparse vectors (Keyword matching).

### B. Retrieval & Reasoning (The "Agentic Brain")
1. **LangGraph State Machine**:
   - *Why*: Standard linear chains are too rigid. LangGraph allows the AI to "loop"—to search, realize it needs more info, and search again.
2. **Tool-Use (Function Calling)**:
   - The agent has a `rag_tool` (to search the vault) and a `list_vault_papers_tool` (to see what's available).
3. **The "Handshake" (Visual Linking)**:
   - When the agent retrieves a chunk, it looks for the `[IMAGE_REFERENCE]` tag. This allows the AI to "see" and "refer" to figures in its response.

### C. Frontend (The "Real-Time Experience")
1. **SSE (Server-Sent Events)**:
   - *Why*: AI tasks take time. Waiting 10 seconds for a response feels like a crash. SSE allows us to "stream" progress (Loading bar) and tokens (Typewriter effect) simultaneously.

---

## 3. Data Isolation (Multi-Tenancy)

We implement **Team-Based Isolation** at two levels:
1. **Database Level**: Every user is linked to a `team_code`.
2. **Vector Level**: Every chunk in Qdrant is tagged with a `tenant_id` (a hash of the team_code).
3. **Security**: The agent *never* sees data outside its tenant. This is enforced at the query level, not the LLM level (Security by Design).
