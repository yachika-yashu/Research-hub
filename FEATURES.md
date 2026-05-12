# ResearchHub — Feature Reference

A complete guide to every researcher feature in ResearchHub, with concrete examples of what each one does.

---

## 1. Conversational Research Chat

**What:** A multi-turn AI assistant that answers questions about papers you've uploaded. Conversations are persistent — you can pick up where you left off across sessions.

**Example:**
> *You:* "What is the core contribution of this paper?"
> *AI:* "The paper proposes a sparse attention mechanism that reduces transformer complexity from O(n²) to O(n log n) by…"
> *You:* "How does that compare to Longformer?"
> *AI:* "Longformer uses a sliding window pattern whereas this work uses…" *(the AI remembers the first answer)*

**Where:** Chat tab → type in the chat input at the bottom.

---

## 2. Bulk Paper Upload with Progress Tracking

**What:** Upload one or many PDFs at once. Each file shows a real-time progress bar as the worker indexes it (extraction → metadata → embeddings → vector store).

**Example:** Select 5 PDFs from your Downloads folder at once. You see:
```
Extracting text from paper1.pdf...   10%
Analyzing paper metadata...          25%
Chunking document...                 45%
Generating dense embeddings...       60%
Indexing into Qdrant vault...        88%
Generating paper summary...          92%
Extracting structured fields...      94%
Indexing complete.                  100%
```

**Where:** Sidebar → "Upload Papers" → select one or more PDFs → "Index N Papers".

---

## 3. BibTeX Export

**What:** Downloads your entire vault as a `.bib` file ready to paste into LaTeX or import into Zotero/Mendeley. Keys are generated automatically in `AuthorYearWord` format (e.g., `Vaswani2017Attention`).

**Example output (`vault.bib`):**
```bibtex
@article{Vaswani2017Attention,
  title  = {Attention Is All You Need},
  author = {Vaswani, Ashish and Shazeer, Noam and ...},
  year   = {2017},
  doi    = {10.48550/arXiv.1706.03762},
}

@article{Brown2020Language,
  title  = {Language Models are Few-Shot Learners},
  ...
}
```

**Where:** Sidebar → "Export BibTeX (.bib)" button (appears when vault is non-empty).

---

## 4. Structured Research Fields (per paper)

**What:** After ingestion, an LLM extracts four structured fields from each paper: main contribution, dataset used, baselines compared against, and known limitations. These are stored and displayed on demand.

**Example (for a vision paper):**
- **Contribution:** A self-supervised pre-training strategy for vision transformers using masked image modeling.
- **Dataset:** ImageNet-21k for pre-training; COCO for fine-tuning.
- **Baselines:** ViT-B/16, DINO, BEiT, MAE.
- **Limitations:** Computationally expensive pre-training; limited to fixed-resolution inputs.

**Where:** Sidebar → paper expander → "Research Details" button.

---

## 5. Extracted Bibliography (Reference List)

**What:** The bibliography at the back of each paper is extracted and stored at ingest time. Each reference shows authors, year, title, and an arXiv ID when detectable — so you can identify and ingest related papers in one click.

**Example:**
```
• Vaswani et al. (2017). Attention Is All You Need — 1706.03762
• Devlin et al. (2018). BERT: Pre-training of Deep Bidirectional Transformers — 1810.04805
• Brown et al. (2020). Language Models are Few-Shot Learners — 2005.14165
```

**Where:** Sidebar → paper expander → "References" button.

---

## 6. Passage Search (Raw Retrieval, No AI)

**What:** Bypasses the language model entirely. Queries the vector store directly and returns the exact text chunks that match — ranked by semantic similarity score. Useful when you need the verbatim text, not an AI paraphrase.

**Example query:** `"attention mechanism dropout regularisation"`

**Example result:**
```
[1] transformer.pdf — score 0.912
"We apply dropout to the output of each sub-layer, before it is added to
the sub-layer input and normalized. In addition, we apply dropout to the
sums of the embeddings and positional encodings..."

[2] bert.pdf — score 0.874
"We use a dropout probability of 0.1 on all layers..."
```

**Where:** Chat tab → toggle "Passages" switch → type search query → "Search".

---

## 7. Literature Review Generator

**What:** Select 2–5 papers and a research question. The AI synthesises a structured literature review spanning all of them — covering methodology, contributions, and open problems — streamed token by token. The result is downloadable as Markdown.

**Example question:** *"How do these papers approach the scalability problem in transformers?"*

**Example output (streamed):**
```markdown
## Overview
Papers A, B, and C all address the quadratic complexity of self-attention...

## Methodology Comparison
| Paper | Approach      | Complexity |
|-------|--------------|------------|
| A     | Sparse attn  | O(n log n) |
| B     | Linear attn  | O(n)       |
| C     | Sliding win  | O(n·k)     |

## Open Problems
All three works acknowledge that sparse patterns are heuristic and...
```

**Where:** Analyze tab → "Literature Review" sub-tab → select papers → enter question → "Generate Review".

---

## 8. Knowledge Graph (Paper Relationship Map)

**What:** Computes edges between all vault papers based on shared authors and shared keywords. Shows which papers come from the same research group or address the same topics — useful for understanding a field's structure.

**Example output:**
```
Papers (nodes): 8
Connections (edges): 5

• transformer.pdf ↔ bert.pdf (shared author: Jacob Devlin)
• bert.pdf ↔ roberta.pdf (shared keywords: masked language model, pretraining)
• gpt3.pdf ↔ instructgpt.pdf (shared author: Alec Radford)
```

**Where:** Analyze tab → "Knowledge Graph" sub-tab → "Build Graph".

---

## 9. Reading Queue

**What:** A lightweight to-do list for papers you want to read but haven't indexed yet. Keeps the vault clean — only truly read and understood papers enter the search index. Track papers through three states: **Queued → Reading → Done**.

**Example workflow:**
1. You spot an interesting paper on Twitter: "Mamba: Linear-Time Sequence Modeling"
2. Add it to the queue with arXiv ID `2312.00752`
3. Later, mark it "Reading" when you start
4. When done, remove it (or ingest it into the vault)

**Where:** Sidebar → "Reading Queue" expander → fill in title + arXiv ID → "Add to Queue". Use the → button to advance status.

---

## 10. Arxiv Keyword Monitoring

**What:** Set keyword watches (e.g. `"RAG retrieval augmented generation 2024"`). The background worker checks Arxiv daily at 07:00 UTC and stores any matching new papers as alerts. No public URL needed — this is outbound polling, like checking email.

**Example:**
- You add monitor: `"mixture of experts sparse LLM`"
- Next morning the worker finds 3 new papers matching that query
- The sidebar shows: **"Arxiv Alerts (3 new)"**
- Each alert shows title, authors, abstract snippet, and arXiv ID
- Click "Add to Vault" to ingest it directly

**Where:** Sidebar → "Arxiv Alerts" expander → "Keyword Monitors" section → enter keywords → "+ Watch".

> **Note on localhost:** The worker polls `export.arxiv.org` from inside Docker. No public IP or webhook is required. The worker process must be running (`docker compose up worker`).

---

## Quote Mode (Highlight Mode)

**What:** Prefix modifier for chat. Instructs the AI to quote exact sentences from the paper before synthesising, rather than paraphrasing freely. Useful for verifying claims.

**Example:**
> Toggle "Quote mode" ON, then ask: *"What learning rate did they use?"*
> AI response: *"The paper states: 'We use a peak learning rate of 1e-4 with a linear warmup of 10,000 steps.' This corresponds to..."*

**Where:** Chat tab → "Quote mode" toggle.

---

## Scope Filter

**What:** Restricts all chat queries and passage searches to a single paper. Useful when your vault has many papers and you want focused answers.

**Example:** Select `attention_is_all_you_need.pdf` from the dropdown, then ask "What is the model size?" — the AI only searches that paper's chunks, ignoring others.

**Where:** Chat tab → dropdown at the top (defaults to "All Papers").

---

## Head-to-Head Paper Comparison

**What:** Ask a specific research question about two papers side by side. Uses the same literature review engine with two papers selected.

**Example:**
- Paper A: `gpt3.pdf`
- Paper B: `palm.pdf`
- Question: *"How do training data and model architecture differ?"*

**Where:** Analyze tab → "Compare Two Papers" sub-tab.

---

## Notes Editor

**What:** A Markdown note editor with Edit/Preview sub-tabs. Notes are stored per user (not per team), support full Markdown, and can be created, edited, or deleted at any time.

**Example:** Paste your reading notes on a paper, write `**Key finding:** ...`, switch to Preview to render it.

**Where:** "My Notes" tab.

---

## Workspace / Usage Dashboard

**What:** Shows how much the system has been used — total tokens consumed, estimated API cost, average faithfulness score, and a timeline of every ingest and query event. Also displays a table of all vault papers with metadata at a glance.

**Where:** "Workspace" tab.
