"""
A/B tester — runs two prompt variants against the same eval questions,
scores faithfulness for each, and runs a paired t-test to confirm
significance. Falls back to a local CSV when LangSmith is unavailable.

Runnable standalone: python -m benchmarking.ab_tester
"""
import asyncio
import csv
import json
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.config import GENERATION_MODEL
from app.core.globals import openai_client
from app.core.logic import verify_faithfulness
from app.services.vector_store import search_vdb
from app.core.logging import logger


# ── Eval question bank (stratified by query_type) ───────────────────────────

_EVAL_QUESTIONS: list[dict] = [
    {"question": "What is this paper about?",                   "query_type": "chat"},
    {"question": "Who are the authors of this work?",           "query_type": "chat"},
    {"question": "What methodology was used?",                  "query_type": "chat"},
    {"question": "What datasets or benchmarks were evaluated?", "query_type": "chat"},
    {"question": "Summarise the main results.",                 "query_type": "chat"},
    {"question": "What are the limitations of this approach?",  "query_type": "literature_review"},
    {"question": "How does this compare to prior work?",        "query_type": "literature_review"},
    {"question": "What future work is suggested?",              "query_type": "literature_review"},
    {"question": "Compare the experimental setups across papers.", "query_type": "comparison"},
    {"question": "Which paper provides the strongest evidence?",   "query_type": "comparison"},
]


@dataclass
class ABRunResult:
    question: str
    query_type: str
    variant_a_answer: str = ""
    variant_b_answer: str = ""
    variant_a_faithfulness: float = 0.0
    variant_b_faithfulness: float = 0.0
    error: Optional[str] = None


@dataclass
class ABTestSummary:
    variant_a_label: str
    variant_b_label: str
    n_questions: int
    a_mean_faith: float
    b_mean_faith: float
    t_statistic: float
    p_value: float
    significant: bool          # p < 0.05
    winner: str                # "A", "B", or "tie"
    per_query: list[dict] = field(default_factory=list)
    csv_path: Optional[str] = None
    langsmith_project: Optional[str] = None


async def _generate_answer(prompt_template: str, question: str, context: str) -> str:
    """Fill the template and call the generation model."""
    filled_prompt = prompt_template.format(question=question, context=context)
    try:
        resp = await openai_client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "user", "content": filled_prompt}],
            temperature=0.1,
            max_tokens=512,
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        logger.warning(f"AB_TESTER: Generation failed — {exc}")
        return ""


async def _run_variant(
    prompt_template: str,
    questions: list[dict],
    tenant_id: str,
) -> list[dict]:
    """Run all questions through a single prompt variant, return per-question dicts."""
    results = []
    for q in questions:
        question = q["question"]
        try:
            context_chunks = await search_vdb(question, tenant_id, limit=5)
            context_text = "\n\n".join(c.get("text", "") for c in context_chunks)
            answer = await _generate_answer(prompt_template, question, context_text)
            faith_report = await verify_faithfulness(
                openai_client, question, answer, context_chunks
            )
            results.append({
                "question": question,
                "query_type": q["query_type"],
                "answer": answer,
                "faithfulness": faith_report.get("faithfulness_score", 0.0),
                "evaluator": faith_report.get("evaluator", "unknown"),
            })
        except Exception as exc:
            logger.warning(f"AB_TESTER: Question '{question[:40]}' failed — {exc}")
            results.append({
                "question": question,
                "query_type": q["query_type"],
                "answer": "",
                "faithfulness": 0.0,
                "error": str(exc),
            })
    return results


def _paired_ttest(a_scores: list[float], b_scores: list[float]) -> tuple[float, float]:
    """Return (t_statistic, p_value) for a paired t-test; returns (0.0, 1.0) on failure."""
    try:
        from scipy.stats import ttest_rel
        t_stat, p_val = ttest_rel(a_scores, b_scores)
        return round(float(t_stat), 6), round(float(p_val), 6)
    except ImportError:
        logger.warning("AB_TESTER: scipy not installed — t-test skipped.")
        return 0.0, 1.0
    except Exception as exc:
        logger.warning(f"AB_TESTER: t-test failed — {exc}")
        return 0.0, 1.0


def _save_csv(results: list[dict], csv_path: str) -> None:
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["question", "query_type", "variant", "faithfulness", "answer", "evaluator", "error"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    logger.info(f"AB_TESTER: Results saved to {csv_path}")


def _log_to_langsmith(summary: ABTestSummary, per_query: list[dict]) -> Optional[str]:
    api_key = os.getenv("LANGCHAIN_API_KEY")
    if not api_key:
        return None
    try:
        from langsmith import Client

        client = Client(api_key=api_key)
        project = os.getenv("LANGCHAIN_PROJECT", "researchhub-ab-test")

        for row in per_query:
            client.create_run(
                name="ab_test",
                run_type="chain",
                project_name=project,
                inputs={"question": row.get("question", "")},
                outputs={
                    "variant": row.get("variant"),
                    "faithfulness": row.get("faithfulness", 0.0),
                    "answer": row.get("answer", "")[:200],
                },
                extra={
                    "query_type": row.get("query_type"),
                    "evaluator": row.get("evaluator"),
                },
            )
        logger.info(f"AB_TESTER: Logged {len(per_query)} runs to LangSmith project '{project}'.")
        return project
    except ImportError:
        logger.info("AB_TESTER: langsmith not installed — skipping LangSmith logging.")
        return None
    except Exception as exc:
        logger.warning(f"AB_TESTER: LangSmith logging failed — {exc}")
        return None


async def run_ab_test(
    variant_a_prompt: str,
    variant_b_prompt: str,
    tenant_id: str,
    variant_a_label: str = "variant_a",
    variant_b_label: str = "variant_b",
    query_types: Optional[list[str]] = None,
    csv_output_path: Optional[str] = None,
) -> ABTestSummary:
    """
    Run a full A/B test comparing two prompt templates.

    Both variants receive the same questions (optionally filtered by query_type)
    and the same retrieved context, so only the prompt wording differs.

    Args:
        variant_a_prompt: Prompt template with {question} and {context} placeholders.
        variant_b_prompt: Same format, different wording.
        tenant_id: Tenant whose vault to query.
        variant_a_label / variant_b_label: Human-readable names for the report.
        query_types: Filter to specific query types (e.g. ["chat", "comparison"]).
        csv_output_path: If set, save full per-question results to this CSV file.

    Returns:
        ABTestSummary with t-test results, winner, and per-query breakdown.
    """
    questions = _EVAL_QUESTIONS
    if query_types:
        questions = [q for q in questions if q["query_type"] in query_types]
    if not questions:
        raise ValueError(f"No questions match query_types={query_types}")

    # Shuffle so order effects cancel across variants
    questions = random.sample(questions, len(questions))

    logger.info(f"AB_TESTER: Running {len(questions)} questions × 2 variants for tenant '{tenant_id}'.")

    a_results, b_results = await asyncio.gather(
        _run_variant(variant_a_prompt, questions, tenant_id),
        _run_variant(variant_b_prompt, questions, tenant_id),
    )

    a_scores = [r["faithfulness"] for r in a_results]
    b_scores = [r["faithfulness"] for r in b_results]

    t_stat, p_val = _paired_ttest(a_scores, b_scores)

    a_mean = round(sum(a_scores) / len(a_scores), 4) if a_scores else 0.0
    b_mean = round(sum(b_scores) / len(b_scores), 4) if b_scores else 0.0
    significant = p_val < 0.05
    if not significant:
        winner = "tie"
    elif a_mean >= b_mean:
        winner = "A"
    else:
        winner = "B"

    # Build combined per-query list for CSV / LangSmith
    per_query: list[dict] = []
    for a, b in zip(a_results, b_results):
        per_query.append({**a, "variant": variant_a_label})
        per_query.append({**b, "variant": variant_b_label})

    csv_path_used = None
    if csv_output_path:
        _save_csv(per_query, csv_output_path)
        csv_path_used = csv_output_path
    else:
        # Default fallback CSV
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        default_path = str(Path(__file__).parent / f"ab_results_{ts}.csv")
        _save_csv(per_query, default_path)
        csv_path_used = default_path

    ls_project = _log_to_langsmith(
        ABTestSummary(
            variant_a_label=variant_a_label,
            variant_b_label=variant_b_label,
            n_questions=len(questions),
            a_mean_faith=a_mean,
            b_mean_faith=b_mean,
            t_statistic=t_stat,
            p_value=p_val,
            significant=significant,
            winner=winner,
        ),
        per_query,
    )

    return ABTestSummary(
        variant_a_label=variant_a_label,
        variant_b_label=variant_b_label,
        n_questions=len(questions),
        a_mean_faith=a_mean,
        b_mean_faith=b_mean,
        t_statistic=t_stat,
        p_value=p_val,
        significant=significant,
        winner=winner,
        per_query=per_query,
        csv_path=csv_path_used,
        langsmith_project=ls_project,
    )


if __name__ == "__main__":
    PROMPT_A = (
        "You are a research assistant. Answer the following question using only "
        "the provided context.\n\nContext:\n{context}\n\nQuestion: {question}\nAnswer:"
    )
    PROMPT_B = (
        "You are an expert academic researcher. Using only the passages below, "
        "provide a precise, evidence-based answer.\n\nPassages:\n{context}\n\n"
        "Research question: {question}\nDetailed answer:"
    )

    tenant_id = os.getenv("BENCHMARK_TENANT_ID", "default")

    print("=" * 55)
    print("  ResearchHub A/B Tester")
    print("=" * 55)
    print(f"\nTenant: {tenant_id}")
    print("Variant A: baseline research assistant prompt")
    print("Variant B: expert academic researcher prompt\n")

    summary = asyncio.run(
        run_ab_test(
            PROMPT_A, PROMPT_B, tenant_id,
            variant_a_label="baseline",
            variant_b_label="expert_academic",
        )
    )

    print(f"\nResults")
    print(f"  Questions tested : {summary.n_questions}")
    print(f"  {summary.variant_a_label:20s} mean faithfulness : {summary.a_mean_faith:.4f}")
    print(f"  {summary.variant_b_label:20s} mean faithfulness : {summary.b_mean_faith:.4f}")
    print(f"  t-statistic      : {summary.t_statistic:.6f}")
    print(f"  p-value          : {summary.p_value:.6f}")
    print(f"  Significant      : {summary.significant}")
    print(f"  Winner           : {summary.winner}")
    print(f"  CSV saved to     : {summary.csv_path}")
    if summary.langsmith_project:
        print(f"  LangSmith project: {summary.langsmith_project}")
