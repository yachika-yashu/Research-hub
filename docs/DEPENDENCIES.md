# 🛠️ The Tech Stack: Choices & Alternatives

## 1. Backend: FastAPI (Python)
*   **Why**: Async by default, high performance, and native support for Pydantic (data validation).
*   **Alternatives**: 
    - *Flask*: Simpler, but blocking (bad for streaming).
    - *Node.js (Express)*: Great for async, but Python has the better AI/ML ecosystem (LangChain, Docling).

## 2. Extraction: Docling (IBM)
*   **Why**: It uses specialized models to detect document structure (headings, tables, pictures). It's much better than `PyPDF2` (raw text only).
*   **Alternatives**:
    - *Unstructured.io*: Very popular, but can be heavy/slow for local use.
    - *Marker*: Great for math/academic papers, but Docling has better object extraction support for visuals.

## 3. Vector Database: Qdrant
*   **Why**: Supports **Hybrid Search** (Dense + Sparse) and **Metadata Filtering** natively. It's written in Rust, making it extremely fast.
*   **Alternatives**:
    - *Pinecone*: SaaS only (no local control).
    - *Chroma*: Very easy to start, but lacks advanced sparse vector support for complex academic queries.

## 4. Orchestration: LangGraph
*   **Why**: It treats AI as a **Cyclic Graph** (State Machine). This allows for error correction—if the AI doesn't find the answer, it can "loop" back and try a different search query.
*   **Alternatives**:
    - *LangChain (Chains)*: Good for linear tasks, but hard to manage complex logic.
    - *CrewAI*: Good for multi-agent, but LangGraph gives more "low-level" control over the state.

## 5. Frontend: Streamlit
*   **Why**: Rapid prototyping. We can build a full AI dashboard in 200 lines of Python.
*   **Alternatives**:
    - *Next.js (React)*: Much better UI flexibility, but requires managing a separate JS/TS codebase.
