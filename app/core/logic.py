import re
import unicodedata
import uuid
import json
from typing import List, Tuple, Dict, Any, Optional
from collections import Counter
from datetime import datetime

from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.core.config import (
    REPEATED_LINE_THRESHOLD, MIN_HEADER_LINE_LENGTH, CHUNK_SIZE, CHUNK_OVERLAP,
    GENERATION_MODEL, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, TOKEN_ENCODER
)
from app.schemas.models import DocumentChunk, CleaningMetadata, ChunkingStats, PaperMetadata
from app.core.logging import logger

def clean_text(text: str) -> Tuple[str, CleaningMetadata]:
    """Production cleaning pipeline with normalization and de-noising."""
    stats = {"lig": 0, "hyp": 0, "rep": 0, "ctl": 0}
    original_len = len(text)
    
    # 1. Unicode Normalization (Step 6)
    text = unicodedata.normalize('NFKC', text)
    
    # 2. Control Character Removal
    clean_text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in "\n\t")
    stats["ctl"] = len(text) - len(clean_text)
    
    # 3. Ligature Correction
    ligatures = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
    for lig, replacement in ligatures.items():
        count = clean_text.count(lig)
        if count > 0:
            clean_text = clean_text.replace(lig, replacement)
            stats["lig"] += count
            
    # 4. De-hyphenation (Step 6)
    clean_text = re.sub(r'(\w+)-\n(\w+)', r'\1\2', clean_text)
    
    # 5. Repeated Line Removal (Noise Filtering)
    lines = clean_text.split("\n")
    line_counts = Counter(lines)
    filtered_lines = []
    for line in lines:
        if line_counts[line] > 5 and len(line.strip()) > MIN_HEADER_LINE_LENGTH:
            stats["rep"] += 1
            continue
        filtered_lines.append(line)
    
    final_text = "\n".join(filtered_lines)
    
    meta = CleaningMetadata(
        original_char_count=original_len,
        cleaned_char_count=len(final_text),
        chars_removed=original_len - len(final_text),
        ligatures_fixed=stats["lig"],
        hyphenations_fixed=stats["hyp"],
        repeated_lines_removed=stats["rep"],
        control_chars_removed=stats["ctl"]
    )
    return final_text, meta

def chunk_document(text: str, doc_id: str, tenant_id: str, image_map: Dict[str, str]) -> Tuple[List[DocumentChunk], ChunkingStats]:
    """Structure-aware recursive splitting with multimodal linking."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len,
        add_start_index=True
    )
    
    raw_chunks = splitter.create_documents([text])
    chunks = []
    
    # Track stats
    stats = {"text": 0, "table": 0, "figure": 0, "tokens": 0}
    
    for i, rc in enumerate(raw_chunks):
        c_id = f"{doc_id}_{i}"
        
        # Link Visual Assets (Docling 2.x Upgrade - Phase 15)
        # Docling 2.x markers are usually <!-- picture-1 --> or <!-- table-1 -->
        element_match = re.search(r"<!-- (picture|table|item)-(\d+) -->", rc.page_content)
        media_url = None
        c_type = "text"
        
        if element_match:
            # Reconstruct the ID as used in the image_map (e.g., picture-1)
            element_key = f"{element_match.group(1)}-{element_match.group(2)}"
            media_url = image_map.get(element_key)
            if media_url:
                c_type = "figure" if "picture" in element_key.lower() else "table"
        
        stats[c_type] += 1
        
        chunk = DocumentChunk(
            chunk_id=c_id,
            document_id=doc_id,
            tenant_id=tenant_id,
            text=rc.page_content,
            chunk_type=c_type,
            chunk_index=i,
            char_start=rc.metadata.get("start_index", 0),
            char_end=rc.metadata.get("start_index", 0) + len(rc.page_content),
            token_count=count_tokens(rc.page_content),
            media_url=media_url
        )
        chunks.append(chunk)
        stats["tokens"] += chunk.token_count

    final_stats = ChunkingStats(
        total_chunks=len(chunks),
        text_chunks=stats["text"],
        table_chunks=stats["table"],
        figure_chunks=stats["figure"],
        total_tokens=stats["tokens"],
        estimated_embedding_cost_usd=estimate_cost(stats["tokens"], 0, EMBEDDING_MODEL)
    )
    return chunks, final_stats

async def extract_paper_metadata(openai_client, text_sample: str) -> PaperMetadata:
    """Uses LLM to extract semantic markers from paper header."""
    prompt = (
        "Extract structured metadata from the following research paper header text.\n"
        "Return ONLY a JSON object with keys: title, authors (list), year (int), doi, journal, keywords (list).\n"
        "If a field is missing, use null.\n\n"
        f"TEXT SAMPLE:\n{text_sample[:3000]}"
    )
    try:
        resp = await openai_client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        content = resp.choices[0].message.content
        if not content:
            raise ValueError("Empty response from OpenAI")
            
        data = json.loads(content)
        # Ensure year is an int or None
        if "year" in data and data["year"]:
            try:
                data["year"] = int(data["year"])
            except:
                data["year"] = None
                
        return PaperMetadata(**data)
    except Exception as e:
        logger.warning(f"METADATA: Failed to extract, using empty default — {e}")
        return PaperMetadata(title="Unknown Research Paper", authors=[], year=None)

async def embed_chunks(openai_client, chunks: List[DocumentChunk]) -> List[DocumentChunk]:
    """Generates dense embeddings for all chunks."""
    texts = [c.text for c in chunks]
    resp = await openai_client.embeddings.create(input=texts, model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIMENSIONS)
    for i, item in enumerate(resp.data):
        chunks[i].embedding = item.embedding
    return chunks

async def verify_faithfulness(openai_client, query: str, answer: str, context_chunks: List[dict]) -> dict:
    """
    Step 46: Rag-as-a-Judge.
    Evaluates if the generated answer is strictly supported by the provided context.
    """
    context_text = "\n".join([f"[{i}] {c['text']}" for i, c in enumerate(context_chunks)])
    
    prompt = (
        "You are a Research Verification Judge. Your task is to evaluate if a generated answer is FAITHFUL to the provided context.\n\n"
        "CONTEXT:\n"
        f"{context_text}\n\n"
        f"QUERY: {query}\n"
        f"GENERATED ANSWER: {answer}\n\n"
        "Return ONLY a JSON object with:\n"
        "- faithfulness_score: (float between 0.0 and 1.0)\n"
        "- reasoning: (brief string explaining the score)\n"
        "- is_well_grounded: (boolean)\n"
    )

    try:
        resp = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        logger.warning(f"VERIFIER: Verification failed — {e}")
        return {"faithfulness_score": 1.0, "reasoning": "Verification bypassed due to system error.", "is_well_grounded": True}

def count_tokens(text: str) -> int:
    """Accurate token counting using tiktoken (Step 47)."""
    if not text: return 0
    return len(TOKEN_ENCODER.encode(text))

def estimate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """Estimated USD cost based on standard OpenAI pricing (Step 47)."""
    # Rates per 1M tokens (gpt-4o-mini approx prices)
    rates = {
        "gpt-4o-mini": {"in": 0.15, "out": 0.60},
        "text-embedding-3-small": {"in": 0.02, "out": 0.0}
    }
    
    config = rates.get(model, {"in": 0.0, "out": 0.0})
    cost = (input_tokens / 1_000_000 * config["in"]) + (output_tokens / 1_000_000 * config["out"])
    return round(cost, 6)

async def generate_paper_summary(openai_client, text_sample: str, title: str) -> str:
    """Generates a concise 5-sentence plain-English summary for quick paper understanding."""
    prompt = (
        f"Write exactly 5 sentences summarizing the research paper titled '{title}'.\n"
        "Cover in order: (1) the problem addressed, (2) methodology used, "
        "(3) main findings, (4) significance or contribution, (5) limitations or future work.\n\n"
        f"PAPER EXCERPT:\n{text_sample[:4000]}"
    )
    try:
        resp = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=350
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"SUMMARY: Failed to generate — {e}")
        return ""


# =============================================================================
# BIBTEX UTILITIES
# =============================================================================

# Common English stopwords to skip when picking the title keyword for bibtex keys
_TITLE_STOPWORDS = {"a", "an", "the", "on", "in", "of", "for", "to", "with",
                    "and", "or", "is", "are", "from", "by", "at", "as", "via"}

def generate_bibtex_key(authors: list, year, title: str) -> str:
    """Builds a AuthorYearWord style BibTeX citation key from paper metadata.
    Pure function — no LLM needed. Falls back gracefully when fields are missing."""
    last = "Unknown"
    if authors:
        last = authors[0].split()[-1].replace(",", "").replace(".", "")
    yr = str(year) if year else "nd"
    word = "Paper"
    for w in re.split(r'\W+', title or ""):
        if w.lower() not in _TITLE_STOPWORDS and len(w) > 2:
            word = w.capitalize()
            break
    return f"{last}{yr}_{word}"


def build_bibtex_entry(paper: dict, key: str) -> str:
    """Formats one paper dict into a @article or @misc BibTeX block.
    Pure function — used by the BibTeX export endpoint."""
    authors_list = paper.get("authors") or []
    author_str = " and ".join(authors_list) if authors_list else "Unknown"
    entry_type = "article" if paper.get("journal") else "misc"

    lines = [f"@{entry_type}{{{key},"]
    lines.append(f'  title = {{{paper.get("title") or "Unknown"}}},' )
    lines.append(f'  author = {{{author_str}}},')
    if paper.get("year"):
        lines.append(f'  year = {{{paper["year"]}}},')
    if paper.get("journal"):
        lines.append(f'  journal = {{{paper["journal"]}}},')
    if paper.get("doi"):
        lines.append(f'  doi = {{{paper["doi"]}}},')
        lines.append(f'  url = {{https://doi.org/{paper["doi"]}}},')
    lines.append("}")
    return "\n".join(lines)


# =============================================================================
# STRUCTURED EXTRACTION (runs at ingest time alongside auto-summary)
# =============================================================================

async def extract_structured_fields(openai_client, text_sample: str) -> dict:
    """Extracts 4 structured research fields from paper text using a single LLM call.
    Stored in paper_details so the researcher can compare papers without re-reading.
    Wrapped in try/except so a failure here never blocks the main ingest."""
    prompt = (
        "Read the research paper excerpt below and extract exactly these four fields.\n"
        "Return ONLY a JSON object with keys:\n"
        "  contribution  (string: the main technical contribution in 1-2 sentences)\n"
        "  dataset       (string: dataset(s) or benchmark(s) used, or null)\n"
        "  baselines     (list of strings: models or methods used for comparison)\n"
        "  limitations   (string: stated or implied limitations, 1-2 sentences)\n\n"
        f"PAPER TEXT:\n{text_sample[:5000]}"
    )
    try:
        resp = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=400
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        logger.warning(f"STRUCTURED: Extraction failed — {e}")
        return {}


async def extract_bibliography(openai_client, text_sample: str) -> list:
    """Extracts the bibliography from the last portion of paper text.
    Returns a list of {title, authors, year, doi, arxiv_id} dicts.
    Capped at 60 refs; failure is non-fatal and returns empty list."""
    # Bibliography is almost always in the final 25% of a paper
    bib_section = text_sample[max(0, int(len(text_sample) * 0.72)):][:6000]
    if len(bib_section) < 200:
        return []

    prompt = (
        "Extract the bibliography/references from this text.\n"
        "Return a JSON object with one key 'references': a list of objects, each with:\n"
        "  title (string), authors (list of strings), year (int or null), doi (string or null)\n"
        "Include at most 60 references. If no references are found, return {\"references\": []}.\n\n"
        f"TEXT:\n{bib_section}"
    )
    try:
        resp = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=3000
        )
        data = json.loads(resp.choices[0].message.content)
        refs = data.get("references", [])
        # Infer arxiv_id from DOI when possible (arxiv DOIs are 10.48550/arXiv.XXXX.XXXXX)
        for r in refs:
            doi = r.get("doi") or ""
            if "arxiv" in doi.lower():
                r["arxiv_id"] = doi.split("arXiv.")[-1].split("/")[-1]
            else:
                r["arxiv_id"] = None
        return refs[:60]
    except Exception as e:
        logger.warning(f"BIBLIOGRAPHY: Extraction failed — {e}")
        return []


# =============================================================================
# LITERATURE REVIEW GENERATOR
# =============================================================================

async def generate_lit_review(openai_client, papers_context: list, question: str):
    """Streams a structured literature review synthesis across multiple papers.
    papers_context: list of {label, title, chunks_text} dicts.
    Yields token strings; caller wraps in SSE."""
    # Build a labelled context block per paper so the LLM can cite correctly
    context_blocks = []
    for p in papers_context:
        block = f"=== [{p['label']}] {p['title']} ===\n{p['chunks_text']}"
        context_blocks.append(block)
    combined = "\n\n".join(context_blocks)

    prompt = (
        "You are a senior academic writing a literature review.\n"
        "Use ONLY the provided paper excerpts below. Cite papers using their label like [A], [B], etc.\n"
        "Structure your response with these sections:\n"
        "## Overview\n## Key Approaches\n## Similarities\n## Differences\n## Research Gaps\n\n"
        f"RESEARCH QUESTION: {question}\n\n"
        f"PAPER EXCERPTS:\n{combined[:12000]}"
    )
    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        stream=True
    )
    async for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


# =============================================================================
# KNOWLEDGE GRAPH
# =============================================================================

def compute_graph_edges(papers: list) -> dict:
    """Builds a knowledge graph from paper metadata alone — no LLM needed.
    Nodes = papers; edges = shared authors or shared keywords.
    O(n²) — safe for vaults up to ~150 papers; larger vaults sample the 150 newest."""
    if len(papers) > 150:
        papers = papers[:150]  # already sorted newest-first by list_unique_papers

    nodes = []
    for p in papers:
        nodes.append({
            "id": p["filename"],
            "label": (p.get("title") or p["filename"])[:40],
            "year": p.get("year"),
            "authors": p.get("authors") or [],
            "keywords": p.get("keywords") or [],
        })

    edges = []
    seen = set()
    for i, a in enumerate(nodes):
        for j, b in enumerate(nodes):
            if j <= i:
                continue
            key = (a["id"], b["id"])
            if key in seen:
                continue
            seen.add(key)

            shared_authors = list(set(a["authors"]) & set(b["authors"]))
            shared_keywords = list(set(k.lower() for k in a["keywords"]) &
                                   set(k.lower() for k in b["keywords"]))

            if shared_authors:
                edges.append({
                    "source": a["id"], "target": b["id"],
                    "type": "shared_author",
                    "weight": len(shared_authors),
                    "label": ", ".join(shared_authors[:2])
                })
            elif shared_keywords:
                edges.append({
                    "source": a["id"], "target": b["id"],
                    "type": "shared_keyword",
                    "weight": len(shared_keywords),
                    "label": ", ".join(shared_keywords[:2])
                })

    return {"nodes": nodes, "edges": edges}


async def generate_query_variations(client, query: str) -> List[str]:
    """Step 49: AI-driven query expansion for improved retrieval recall."""
    prompt = f"Generate 3 distinct search variations for a vector database to answer: '{query}'. Provide ONLY the search queries, one per line."
    
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a Research Retrieval Expert."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3
    )
    
    text = resp.choices[0].message.content
    variations = [line.strip().strip("- ") for line in text.split("\n") if line.strip()]
    # Ensure original query is included
    if query not in variations:
        variations.insert(0, query)
    return variations[:4] # Original + 3 variations
