"""
Complexity classifier — heuristic-only query routing.

Returns a RoutingDecision describing complexity and a suggested generation
model. The caller always decides whether to act on it. This module never
calls the LLM, reads the vector store, or modifies globals / config.

Runnable standalone: python -m benchmarking.model_router
"""
import asyncio
import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.core.config import GENERATION_MODEL
from app.core.logging import logger


class Complexity(str, Enum):
    SIMPLE    = "simple"
    MULTI_HOP = "multi_hop"
    COMPLEX   = "complex"


@dataclass
class RoutingDecision:
    complexity: Complexity
    suggested_model: str
    token_count: int
    reasons: list


# Ops can override the model for each tier without touching code.
_MODEL_SIMPLE    = os.getenv("ROUTER_MODEL_SIMPLE",    GENERATION_MODEL)
_MODEL_MULTI_HOP = os.getenv("ROUTER_MODEL_MULTI_HOP", GENERATION_MODEL)
_MODEL_COMPLEX   = os.getenv("ROUTER_MODEL_COMPLEX",   "gpt-4o")

# Multi-hop / cross-paper signal words
_MULTI_HOP_RE = re.compile(
    r"\bcompare\b"
    r"|\bvs\.?\b"
    r"|\bversus\b"
    r"|\bacross\s+papers\b"
    r"|\ball\s+papers\b"
    r"|\bmultiple\s+papers\b"
    r"|\beach\s+paper\b"
    r"|\bboth\s+papers\b"
    r"|\bsynthesize\b|\bsynthesis\b"
    r"|\bliterature\s+review\b"
    r"|\brelat(?:ed|ion)\s+(?:to|between)\b"
    r"|\bbetween\s+(?:the\s+)?papers\b"
    r"|\bcommonalities\b"
    r"|\bthematically\b",
    re.IGNORECASE,
)

# Simple factual lookup — anchored to the start of the query
_SIMPLE_RE = re.compile(
    r"^(?:"
    r"who\s+(?:is|are|wrote|authored)"
    r"|what\s+is\b"
    r"|what\s+are\b"
    r"|when\s+(?:was|did|is)\b"
    r"|where\s+(?:was|is|did)\b"
    r"|define\b"
    r"|list\s+the\b"
    r"|name\s+the\b"
    r")",
    re.IGNORECASE,
)


def _token_estimate(text: str) -> int:
    """Whitespace token count — 1 token ≈ 0.75 words, minimum 1."""
    return max(1, int(len(text.split()) / 0.75))


def _multiple_paper_refs(query: str) -> bool:
    """
    True when the query explicitly references 2+ distinct papers.

    Three independent signals (any one triggers):
    - Two or more quoted strings  ("attention is all you need" and "bert")
    - Two or more ordinal/letter paper labels  (Paper A … Paper B)
    - Two or more PDF/arXiv-style identifiers
    """
    if len(re.findall(r'"[^"]+"', query)) >= 2:
        return True

    labels = re.findall(r'\bpaper\s+[A-Z]\b', query, re.IGNORECASE)
    if len(set(l.lower() for l in labels)) >= 2:
        return True

    ids = re.findall(
        r'\b\w[\w\-]+\.pdf\b|\barXiv:\s*\d{4}\.\d{4,5}\b',
        query, re.IGNORECASE,
    )
    if len(ids) >= 2:
        return True

    return False


def classify(query: str) -> RoutingDecision:
    """
    Classify query complexity using three heuristics in strict priority order.

    Priority:
      1. token_count > 200           → complex   (long = likely synthesis)
      2. Multiple paper references   → multi_hop (cross-paper retrieval needed)
      3. Multi-hop linguistic signal → multi_hop
      4. Simple factual pattern      → simple
      5. Default                     → simple    (conservative — avoids wasted spend)

    Never raises; returns a RoutingDecision with reasons explaining the choice.
    """
    query = (query or "").strip()
    token_count = _token_estimate(query)
    reasons: list[str] = []

    if token_count > 200:
        reasons.append(f"token_count={token_count} exceeds 200")
        return RoutingDecision(
            complexity=Complexity.COMPLEX,
            suggested_model=_MODEL_COMPLEX,
            token_count=token_count,
            reasons=reasons,
        )

    if _multiple_paper_refs(query):
        reasons.append("multiple paper references detected")
        return RoutingDecision(
            complexity=Complexity.MULTI_HOP,
            suggested_model=_MODEL_MULTI_HOP,
            token_count=token_count,
            reasons=reasons,
        )

    m = _MULTI_HOP_RE.search(query)
    if m:
        reasons.append(f"multi-hop keyword: '{m.group()}'")
        return RoutingDecision(
            complexity=Complexity.MULTI_HOP,
            suggested_model=_MODEL_MULTI_HOP,
            token_count=token_count,
            reasons=reasons,
        )

    m = _SIMPLE_RE.match(query)
    if m:
        reasons.append(f"simple lookup pattern: '{m.group().strip()}'")
        return RoutingDecision(
            complexity=Complexity.SIMPLE,
            suggested_model=_MODEL_SIMPLE,
            token_count=token_count,
            reasons=reasons,
        )

    reasons.append("no complexity signal; defaulting to simple")
    return RoutingDecision(
        complexity=Complexity.SIMPLE,
        suggested_model=_MODEL_SIMPLE,
        token_count=token_count,
        reasons=reasons,
    )


async def benchmark_routing(eval_dataset_path: str, tenant_id: str = "default") -> dict:
    """
    Run every eval question through the heuristic classifier and log the
    complexity distribution + model breakdown to MLflow.

    The cost/quality tradeoff is implicit: SIMPLE → cheapest model,
    COMPLEX → strongest model. The benchmark shows how often each tier
    would be invoked, letting ops estimate expected cost per query mix.

    Returns a summary dict; never raises on MLflow failure.
    """
    path = Path(eval_dataset_path)
    with open(path) as fh:
        dataset: list[dict] = json.load(fh)

    per_question: list[dict] = []
    complexity_counts: dict[str, int] = {}
    model_counts: dict[str, int] = {}

    for entry in dataset:
        question = entry.get("question", "")
        decision = classify(question)
        cname = decision.complexity.value
        complexity_counts[cname] = complexity_counts.get(cname, 0) + 1
        model_counts[decision.suggested_model] = (
            model_counts.get(decision.suggested_model, 0) + 1
        )
        per_question.append({
            "id":              entry.get("id", ""),
            "question":        question,
            "complexity":      cname,
            "suggested_model": decision.suggested_model,
            "token_count":     decision.token_count,
            "reasons":         decision.reasons,
        })

    summary = {
        "total":              len(dataset),
        "complexity_counts":  complexity_counts,
        "model_distribution": model_counts,
        "per_question":       per_question,
    }

    try:
        import mlflow
        mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000"))
        mlflow.set_experiment("researchhub-routing")
        with mlflow.start_run(run_name="routing-benchmark"):
            for cname, count in complexity_counts.items():
                mlflow.log_metric(f"complexity_{cname}", count)
            for model, count in model_counts.items():
                safe = re.sub(r"[^a-zA-Z0-9_]", "_", model)
                mlflow.log_metric(f"model_{safe}", count)
            mlflow.log_metric("total_questions", len(dataset))
        logger.info("MODEL_ROUTER: Routing benchmark logged to MLflow.")
    except ImportError:
        logger.info("MODEL_ROUTER: mlflow not installed — skipping MLflow log.")
    except Exception as exc:
        logger.warning(f"MODEL_ROUTER: MLflow logging failed — {exc}")

    return summary


if __name__ == "__main__":
    import json as _json

    eval_path = str(Path(__file__).parent / "eval_dataset.json")
    tenant_id = os.getenv("BENCHMARK_TENANT_ID", "default")

    print("=" * 60)
    print("  ResearchHub Model Router — Heuristic Classifier")
    print("=" * 60)

    with open(eval_path) as fh:
        dataset = _json.load(fh)

    print(f"\nClassifying {len(dataset)} eval questions:\n")
    print(f"  {'ID':<22} {'Complexity':<12} {'Tokens':>6}  Reason")
    print(f"  {'-' * 65}")
    for entry in dataset:
        q = entry.get("question", "")
        d = classify(q)
        print(
            f"  {entry.get('id', ''):<22} {d.complexity.value:<12} "
            f"{d.token_count:>6}  {d.reasons[0]}"
        )

    print("\n" + "=" * 60)
    print("  Routing Benchmark (complexity + cost distribution)")
    print("=" * 60 + "\n")
    result = asyncio.run(benchmark_routing(eval_path, tenant_id))
    print(f"  Complexity distribution : {result['complexity_counts']}")
    print(f"  Model distribution      : {result['model_distribution']}")
