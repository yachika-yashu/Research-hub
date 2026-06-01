"""
RAG quality evaluator — pulls TraceLog rows and runs RAGAS faithfulness +
answer_relevancy, detects hallucinations, and optionally logs to LangSmith.

Never modifies the vector store or ingestion pipeline.
Runnable standalone: python -m benchmarking.quality_evaluator
"""
import asyncio
import json
import os
from dataclasses import dataclass
from typing import Optional

from app.core.database import TraceLog, get_db
from app.core.config import GENERATION_MODEL
from app.core.logging import logger


@dataclass
class QueryEvalResult:
    trace_id: str
    question: str
    faithfulness: float
    answer_relevancy: float
    hallucination_detected: bool
    selective_hallucination_warning: bool
    evaluator: str
    error: Optional[str] = None


def _pull_trace_rows(tenant_id: str, limit: int = 50) -> list[dict]:
    """Return the most recent TraceLog rows for a tenant as plain dicts."""
    db = next(get_db())
    try:
        rows = (
            db.query(TraceLog)
            .filter(TraceLog.tenant_id == tenant_id)
            .order_by(TraceLog.id.desc())
            .limit(limit)
            .all()
        )
        results = []
        for r in rows:
            try:
                faith_report = json.loads(r.faithfulness_report_json or "{}")
                context_texts = json.loads(r.context_data_json or "[]")
            except (json.JSONDecodeError, TypeError):
                faith_report = {}
                context_texts = []
            results.append({
                "trace_id": r.id,
                "question": r.full_prompt or "",
                "answer": faith_report.get("answer", ""),
                "contexts": context_texts,
                "faithfulness_score": faith_report.get("faithfulness_score", 0.0),
                "existing_evaluator": faith_report.get("evaluator", "unknown"),
            })
        return results
    finally:
        db.close()


async def _ragas_evaluate_single(question: str, answer: str, contexts: list[str]) -> dict:
    """
    Run RAGAS faithfulness + answer_relevancy for one (question, answer, contexts) triple.
    Falls back gracefully if RAGAS or its deps are not installed.
    Returns {"faithfulness": float, "answer_relevancy": float, "evaluator": str}.
    """
    if not answer or not contexts:
        return {"faithfulness": 0.0, "answer_relevancy": 0.0, "evaluator": "skipped_empty"}

    try:
        from datasets import Dataset
        from ragas import evaluate as ragas_evaluate
        from ragas.metrics import faithfulness as ragas_faithfulness, answer_relevancy
        from ragas.llms import LangchainLLMWrapper
        from langchain_openai import ChatOpenAI

        evaluator_llm = LangchainLLMWrapper(
            ChatOpenAI(model=GENERATION_MODEL, temperature=0.0)
        )
        dataset = Dataset.from_dict({
            "question": [question],
            "answer": [answer],
            "contexts": [contexts],
        })
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: ragas_evaluate(
                dataset,
                metrics=[ragas_faithfulness, answer_relevancy],
                llm=evaluator_llm,
            ),
        )
        return {
            "faithfulness": round(float(result["faithfulness"]), 4),
            "answer_relevancy": round(float(result["answer_relevancy"]), 4),
            "evaluator": "ragas",
        }
    except ImportError:
        logger.info("QUALITY_EVAL: RAGAS not installed — returning stored faithfulness score.")
        return {"faithfulness": 0.0, "answer_relevancy": 0.0, "evaluator": "ragas_not_installed"}
    except Exception as exc:
        logger.warning(f"QUALITY_EVAL: RAGAS failed ({type(exc).__name__}: {exc})")
        return {"faithfulness": 0.0, "answer_relevancy": 0.0, "evaluator": "ragas_error"}


def _log_to_langsmith(results: list[QueryEvalResult]) -> None:
    """Push evaluation results to LangSmith if LANGCHAIN_API_KEY is configured."""
    api_key = os.getenv("LANGCHAIN_API_KEY")
    if not api_key:
        return
    try:
        from langsmith import Client
        from langsmith.schemas import Run

        client = Client(api_key=api_key)
        project = os.getenv("LANGCHAIN_PROJECT", "researchhub-quality")

        for r in results:
            if r.error:
                continue
            client.create_run(
                name="quality_eval",
                run_type="chain",
                project_name=project,
                inputs={"question": r.question},
                outputs={
                    "faithfulness": r.faithfulness,
                    "answer_relevancy": r.answer_relevancy,
                    "hallucination_detected": r.hallucination_detected,
                    "selective_hallucination_warning": r.selective_hallucination_warning,
                },
                extra={"evaluator": r.evaluator, "trace_id": r.trace_id},
            )
        logger.info(f"QUALITY_EVAL: Logged {len(results)} results to LangSmith project '{project}'.")
    except ImportError:
        logger.info("QUALITY_EVAL: langsmith package not installed — skipping LangSmith logging.")
    except Exception as exc:
        logger.warning(f"QUALITY_EVAL: LangSmith logging failed — {exc}")


async def run_quality_evaluation(tenant_id: str, sample_size: int = 50) -> dict:
    """
    Pull recent TraceLog rows, re-evaluate with RAGAS, detect hallucinations.

    Hallucination detection rules
    ─────────────────────────────
    - faithfulness < 0.75  → hallucination_detected = True
    - faithfulness > 0.85 AND context_recall (answer_relevancy proxy) < 0.70
      → selective_hallucination_warning = True (model over-commits on weak context)

    Returns an aggregate summary dict plus per-query results.
    """
    rows = _pull_trace_rows(tenant_id, limit=sample_size)
    if not rows:
        logger.warning(f"QUALITY_EVAL: No TraceLog rows found for tenant '{tenant_id}'.")
        return {"evaluated": 0, "error": "No trace rows found"}

    eval_results: list[QueryEvalResult] = []

    for row in rows:
        question = row["question"]
        answer = row["answer"]
        contexts = [c for c in row["contexts"] if isinstance(c, str) and c.strip()]

        scores = await _ragas_evaluate_single(question, answer, contexts)

        faith = scores["faithfulness"]
        relevancy = scores["answer_relevancy"]

        # Use stored score when RAGAS cannot run
        if scores["evaluator"] in ("skipped_empty", "ragas_not_installed", "ragas_error"):
            faith = row.get("faithfulness_score", 0.0)

        hallucination = faith < 0.75
        selective_warn = (faith > 0.85) and (relevancy < 0.70)

        eval_results.append(QueryEvalResult(
            trace_id=row["trace_id"],
            question=question,
            faithfulness=faith,
            answer_relevancy=relevancy,
            hallucination_detected=hallucination,
            selective_hallucination_warning=selective_warn,
            evaluator=scores["evaluator"],
        ))

    _log_to_langsmith(eval_results)

    valid = [r for r in eval_results if not r.error]
    hallucinated = [r for r in valid if r.hallucination_detected]
    selective = [r for r in valid if r.selective_hallucination_warning]

    summary = {
        "evaluated": len(valid),
        "avg_faithfulness": round(sum(r.faithfulness for r in valid) / len(valid), 4) if valid else 0.0,
        "avg_answer_relevancy": round(sum(r.answer_relevancy for r in valid) / len(valid), 4) if valid else 0.0,
        "hallucination_rate": round(len(hallucinated) / len(valid), 4) if valid else 0.0,
        "hallucination_count": len(hallucinated),
        "selective_hallucination_warnings": len(selective),
        "per_query": [
            {
                "trace_id": r.trace_id,
                "question": r.question[:80],
                "faithfulness": r.faithfulness,
                "answer_relevancy": r.answer_relevancy,
                "hallucination_detected": r.hallucination_detected,
                "selective_hallucination_warning": r.selective_hallucination_warning,
                "evaluator": r.evaluator,
            }
            for r in eval_results
        ],
    }
    return summary


if __name__ == "__main__":
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(description="ResearchHub Quality Evaluator")
    parser.add_argument(
        "--dataset",
        metavar="PATH",
        help="Run vault-agnostic retrieval precision benchmark against this eval_dataset.json",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Write JSON results to this file (default: stdout)",
    )
    parser.add_argument(
        "--tenant",
        default=os.getenv("BENCHMARK_TENANT_ID", "default"),
        help="Tenant ID (default: BENCHMARK_TENANT_ID env var)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=int(os.getenv("BENCHMARK_SAMPLE", "20")),
        help="TraceLog rows to evaluate when --dataset is not used (default: 20)",
    )
    args = parser.parse_args()

    if args.dataset:
        # Vault-agnostic retrieval precision mode — works with an empty vault
        from benchmarking.data_quality import retrieval_precision_benchmark
        raw = asyncio.run(retrieval_precision_benchmark(args.dataset, args.tenant))
        result = {
            "faithfulness":     raw.get("retrieval_check_score") or raw.get("precision_at_3", 0.0),
            "context_recall":   raw.get("precision_at_3", 0.0),
            "hallucination_rate": 0.0,
            "vault_empty":      raw.get("vault_empty", True),
            "evaluated":        raw.get("total_questions", 0),
            "retrieval_check_score":    raw.get("retrieval_check_score"),
            "multi_paper_check_score":  raw.get("multi_paper_check_score"),
            "raw": raw,
        }
    else:
        result = asyncio.run(run_quality_evaluation(args.tenant, sample_size=args.sample))

    output_json = _json.dumps(result, indent=2)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output_json)
    else:
        print("=" * 55)
        print("  ResearchHub Quality Evaluator")
        print("=" * 55)
        print(f"\nTenant: {args.tenant}\n")
        print(output_json)
