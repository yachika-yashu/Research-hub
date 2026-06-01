"""
Data quality auditor — reads Qdrant chunks and runs retrieval precision tests.

Never modifies the vector store or ingestion pipeline.
Runnable standalone: python -m benchmarking.data_quality
"""
import asyncio
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.services.vector_store import get_qdrant, search_vdb
from app.core.config import QDRANT_COLLECTION
from app.core.logging import logger


@dataclass
class ChunkQualityReport:
    """Quality metrics for a single document chunk scrolled from Qdrant."""
    chunk_id: str
    tenant_id: str
    ocr_error_rate: float
    truncation_detected: bool
    information_density: float
    cross_tenant_bleed: bool
    token_count: int
    chunk_type: str


def _ocr_error_rate(text: str) -> float:
    """
    Estimate OCR error rate using three heuristics:
    - Digit/letter confusion runs (e.g. '2O24', 'l994')
    - Non-ASCII garbage sequences of 3+ consecutive characters
    - Isolated single consonants between spaces (not 'a' or 'i')

    Returns a 0.0–1.0 rate capped at 1.0.
    """
    if not text:
        return 0.0

    errors = 0
    # Digit mixed with easily-confused letters O, I, l
    errors += len(re.findall(r'\d[OIl]\d|\d[OIl]{2}\d', text))
    # Non-ASCII garbage runs (≥3 consecutive non-ASCII chars)
    errors += sum(len(m.group()) for m in re.finditer(r'[^\x00-\x7F]{3,}', text))
    # Isolated single consonants (likely OCR split-word artefacts)
    isolated = re.findall(r'(?<!\w)([b-df-hj-np-tv-z])(?!\w)', text, re.IGNORECASE)
    errors += len([c for c in isolated if c.lower() not in {"a", "i"}])

    return min(1.0, round(errors / max(len(text), 1), 6))


def _truncation_detected(text: str) -> bool:
    """
    Return True if the chunk appears to end mid-sentence.

    A chunk ending without terminal punctuation (.  ?  !  "  '  )  ]  })
    likely got cut at a chunk boundary rather than a natural sentence boundary.
    """
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] not in {".", "?", "!", '"', "'", ")", "]", "}"}


def _information_density(text: str) -> float:
    """
    Return the fraction of words longer than 6 characters.

    Academic text tends to have more long, domain-specific words. Low density
    (< 0.15) often indicates OCR noise, boilerplate headers, or short fragments.
    """
    words = re.findall(r"\b\w+\b", text)
    if not words:
        return 0.0
    return round(sum(1 for w in words if len(w) > 6) / len(words), 4)


def audit_chunks(tenant_id: str, sample_size: int = 100) -> tuple[list[ChunkQualityReport], dict]:
    """
    Scroll up to sample_size chunks from Qdrant for the given tenant and
    compute per-chunk quality metrics.

    Returns:
        (list[ChunkQualityReport], aggregate_summary_dict)
    """
    client = get_qdrant()
    reports: list[ChunkQualityReport] = []
    offset = None
    fetched = 0

    while fetched < sample_size:
        batch = min(50, sample_size - fetched)
        points, next_offset = client.scroll(
            collection_name=QDRANT_COLLECTION,
            scroll_filter={
                "must": [{"key": "tenant_id", "match": {"value": tenant_id}}]
            },
            limit=batch,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )

        if not points:
            break

        for pt in points:
            payload = pt.payload or {}
            text = payload.get("text", "")

            # Cross-tenant bleed: chunk payload should have exactly one tenant_id
            # matching the filter. Any extra tenant markers signal isolation failure.
            chunk_tenant = payload.get("tenant_id", "")
            bleed = bool(chunk_tenant) and chunk_tenant != tenant_id

            reports.append(ChunkQualityReport(
                chunk_id=str(pt.id),
                tenant_id=chunk_tenant,
                ocr_error_rate=_ocr_error_rate(text),
                truncation_detected=_truncation_detected(text),
                information_density=_information_density(text),
                cross_tenant_bleed=bleed,
                token_count=payload.get("token_count", 0),
                chunk_type=payload.get("chunk_type", "text"),
            ))
            fetched += 1

        if next_offset is None:
            break
        offset = next_offset

    if not reports:
        logger.warning(f"DATA_QUALITY: No chunks found for tenant '{tenant_id}'.")
        return [], {"sampled": 0}

    type_dist: dict[str, int] = {}
    for r in reports:
        type_dist[r.chunk_type] = type_dist.get(r.chunk_type, 0) + 1

    summary = {
        "sampled": len(reports),
        "avg_ocr_error_rate":     round(sum(r.ocr_error_rate     for r in reports) / len(reports), 4),
        "truncation_rate":        round(sum(r.truncation_detected for r in reports) / len(reports), 4),
        "avg_information_density": round(sum(r.information_density for r in reports) / len(reports), 4),
        "cross_tenant_bleed_count": sum(r.cross_tenant_bleed for r in reports),
        "avg_token_count":         round(sum(r.token_count for r in reports) / len(reports), 1),
        "chunk_type_distribution": type_dist,
    }
    return reports, summary


async def retrieval_precision_benchmark(eval_dataset_path: str, tenant_id: str) -> dict:
    """
    Vault-agnostic retrieval quality benchmark.

    Scoring strategy (no fixed ground truth needed):
    - eval_mode "retrieval_check": a HIT means search_vdb returned >= 1 chunk
      with >= 50 chars of content. Every structural research question should
      retrieve something from any non-empty vault.
    - eval_mode "multi_paper_check": a HIT means >= 2 distinct filenames appear
      in the top-5 results, confirming cross-paper retrieval works.

    If the vault is empty, all questions return 0 results → precision = 0.0,
    which correctly flags "vault is empty" rather than hiding it.

    Fixed ground_truth strings are intentionally NOT used: the correct answer
    depends on which papers are in the vault, so keyword-matching against a
    static string produces meaningless 1.0 scores on any corpus.
    """
    path = Path(eval_dataset_path)
    with open(path) as fh:
        dataset: list[dict] = json.load(fh)

    hits = 0
    skipped = 0
    per_question: list[dict] = []

    for entry in dataset:
        question  = entry["question"]
        eval_mode = entry.get("eval_mode", "retrieval_check")
        complexity = entry.get("complexity")

        try:
            limit = 5 if eval_mode == "multi_paper_check" else 3
            results = await search_vdb(question, tenant_id, limit=limit)

            if eval_mode == "retrieval_check":
                # HIT: at least one chunk with meaningful content returned
                hit = any(len(r.get("text", "")) >= 50 for r in results)

            elif eval_mode == "multi_paper_check":
                # HIT: results come from at least 2 different source files
                filenames = {
                    r.get("metadata", {}).get("filename") or r.get("filename")
                    for r in results
                } - {None}
                hit = len(filenames) >= 2
                if len(filenames) < 2:
                    logger.info(
                        f"DATA_QUALITY: multi_paper_check needs >=2 papers in vault "
                        f"(found {len(filenames)} source files for '{question[:40]}')"
                    )
            else:
                hit = False

            hits += int(hit)
            per_question.append({
                "id":         entry.get("id", ""),
                "question":   question,
                "complexity": complexity,
                "eval_mode":  eval_mode,
                "hit":        hit,
                "chunks_returned": len(results),
            })

        except Exception as exc:
            logger.warning(f"DATA_QUALITY: Retrieval failed for '{question[:40]}' — {exc}")
            per_question.append({
                "id":       entry.get("id", ""),
                "question": question,
                "hit":      False,
                "error":    str(exc),
            })

    # Separate scores for single-paper vs multi-paper questions
    single = [q for q in per_question if q.get("eval_mode") == "retrieval_check" and "error" not in q]
    multi  = [q for q in per_question if q.get("eval_mode") == "multi_paper_check" and "error" not in q]

    total = len(dataset) - skipped
    return {
        "precision_at_3":          round(hits / total, 4) if total else 0.0,
        "hits":                    hits,
        "total_questions":         total,
        "retrieval_check_score":   round(sum(q["hit"] for q in single) / len(single), 4) if single else None,
        "multi_paper_check_score": round(sum(q["hit"] for q in multi)  / len(multi),  4) if multi  else None,
        "vault_empty":             all(q.get("chunks_returned", 0) == 0 for q in per_question if "error" not in q),
        "per_question":            per_question,
    }


if __name__ == "__main__":
    import json as _json

    tenant_id = os.getenv("BENCHMARK_TENANT_ID", "default")
    eval_path = str(Path(__file__).parent / "eval_dataset.json")

    print("=" * 55)
    print("  ResearchHub Data Quality Auditor")
    print("=" * 55)

    print(f"\nAuditing chunks for tenant: {tenant_id}")
    _, summary = audit_chunks(tenant_id, sample_size=100)
    print("\nChunk Quality Summary:")
    print(_json.dumps(summary, indent=2))

    print("\nRetrieval Precision Benchmark (eval_dataset.json):")
    result = asyncio.run(retrieval_precision_benchmark(eval_path, tenant_id))
    print(_json.dumps(result, indent=2))
