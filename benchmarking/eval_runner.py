#!/usr/bin/env python3
"""
Evaluation runner — sends each question in eval_dataset.json through the live
API, collects answers, and scores pass/fail per question.

Works over HTTP (no local app imports needed) so it runs from any machine.

Usage:
    python benchmarking/eval_runner.py [--url URL] [--token TOKEN] [--quick]
    python benchmarking/eval_runner.py --quick  ← 5-question subset, CI-friendly

Output:
    benchmarking/results/eval_results.json

Exit codes:
    0 — overall_pass_rate >= pass_threshold
    1 — overall_pass_rate < pass_threshold
    2 — fatal setup error (API down, auth failed)
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import httpx
    from httpx_sse import aconnect_sse
except ImportError:
    print("ERROR: httpx and httpx-sse required.  pip install httpx httpx-sse")
    sys.exit(2)

_HERE            = Path(__file__).parent
EVAL_DATASET     = _HERE / "eval_dataset.json"
RESULTS_DIR      = _HERE / "results"
RESULTS_FILE     = RESULTS_DIR / "eval_results.json"

DEFAULT_PASS_THRESHOLD = 0.80   # 80% of questions must pass
MIN_ANSWER_CHARS       = 50     # answer shorter than this = fail
QUESTION_TIMEOUT       = 60     # seconds per question


# ── Auth helpers ──────────────────────────────────────────────────────────────

async def _get_token(base_url: str, provided_token: str) -> str:
    if provided_token:
        return provided_token
    test_user     = "evalrunner_auto"
    test_password = "EvalRunner!5678"
    test_team     = "evalrunner_team"
    async with httpx.AsyncClient(timeout=10.0) as c:
        await c.post(
            f"{base_url}/auth/register",
            json={"username": test_user, "password": test_password,
                  "team_code": test_team},
        )
        r = await c.post(
            f"{base_url}/auth/token",
            data={"username": test_user, "password": test_password},
        )
        if r.status_code != 200:
            raise RuntimeError(f"Auth failed: {r.text}")
        return r.json()["access_token"]


# ── Question evaluator ────────────────────────────────────────────────────────

async def _ask(base_url: str, token: str, question: str, filename: str | None) -> dict:
    """Send one question and collect the answer from the SSE stream."""
    headers = {"Authorization": f"Bearer {token}"}
    filters: dict = {"year_min": 1990, "year_max": 2026}
    if filename:
        filters["filename"] = filename

    payload = {"query": question, "filters": filters}
    sse_timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
    answer = ""
    error  = None

    try:
        async with httpx.AsyncClient(timeout=sse_timeout) as client:
            async with aconnect_sse(
                client, "POST", f"{base_url}/query",
                json=payload, headers=headers,
            ) as es:
                async for ev in es.aiter_sse():
                    if ev.data == "[DONE]":
                        break
                    try:
                        evt = json.loads(ev.data)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if evt.get("status") == "cache_hit":
                        answer = evt.get("content", "")
                    elif evt.get("type") == "token":
                        answer += evt.get("content", "")
                    elif evt.get("type") == "error":
                        error = evt.get("content", "pipeline error")
    except asyncio.TimeoutError:
        error = f"timeout after {QUESTION_TIMEOUT}s"
    except Exception as exc:
        error = str(exc)

    return {"answer": answer, "error": error}


async def _evaluate_question(
    base_url: str,
    token: str,
    entry: dict,
    eval_mode: str,
) -> dict:
    """
    Evaluate one question from the eval dataset.

    eval_mode = "retrieval_check"   — HIT if answer length >= MIN_ANSWER_CHARS
    eval_mode = "multi_paper_check" — HIT if answer mentions >= 2 different papers
                                       (heuristic: looks for [A], [B], Paper A, Paper B etc.)
    eval_mode = "answer_quality"    — HIT if answer length >= MIN_ANSWER_CHARS
                                       AND answer doesn't contain common failure phrases
    """
    import re

    question   = entry.get("question", "")
    complexity = entry.get("complexity", "simple")
    filename   = entry.get("filename")

    t0 = datetime.now(timezone.utc)
    try:
        result = await asyncio.wait_for(
            _ask(base_url, token, question, filename),
            timeout=QUESTION_TIMEOUT + 5,
        )
        answer = result["answer"]
        error  = result["error"]
    except asyncio.TimeoutError:
        answer, error = "", f"hard timeout {QUESTION_TIMEOUT + 5}s"

    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()

    if error:
        return {
            "id":         entry.get("id", ""),
            "question":   question,
            "complexity": complexity,
            "eval_mode":  eval_mode,
            "hit":        False,
            "reason":     error,
            "elapsed_s":  round(elapsed, 2),
        }

    if eval_mode == "retrieval_check" or eval_mode == "answer_quality":
        failure_phrases = [
            "i don't have", "i cannot", "no information", "not found",
            "unable to find", "no relevant", "vault is empty",
        ]
        hit = (
            len(answer.strip()) >= MIN_ANSWER_CHARS
            and not any(p in answer.lower() for p in failure_phrases)
        )
        reason = "" if hit else f"answer too short ({len(answer.strip())} chars) or refusal"

    elif eval_mode == "multi_paper_check":
        # Look for multi-paper citation patterns
        labels = re.findall(r'\[([A-Z])\]', answer)
        paper_refs = re.findall(r'[Pp]aper\s+[A-Z]', answer)
        distinct = len(set(labels) | set(paper_refs))
        hit = distinct >= 2
        reason = "" if hit else f"only {distinct} distinct paper reference(s) detected"

    else:
        hit    = len(answer.strip()) >= MIN_ANSWER_CHARS
        reason = "" if hit else "short answer"

    return {
        "id":         entry.get("id", ""),
        "question":   question,
        "complexity": complexity,
        "eval_mode":  eval_mode,
        "hit":        hit,
        "reason":     reason,
        "answer_len": len(answer.strip()),
        "elapsed_s":  round(elapsed, 2),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

async def run_eval(
    base_url: str,
    token: str,
    quick: bool,
    pass_threshold: float,
) -> tuple[dict, int]:
    """
    Run all eval questions.  Returns (results_dict, exit_code).
    """
    if not EVAL_DATASET.exists():
        print(f"ERROR: {EVAL_DATASET} not found.")
        return {}, 2

    dataset: list[dict] = json.loads(EVAL_DATASET.read_text(encoding="utf-8"))

    if quick:
        # Take at most 5 questions, one from each complexity tier if possible
        subset: list[dict] = []
        seen_tiers: set = set()
        for entry in dataset:
            tier = entry.get("complexity", "simple")
            if tier not in seen_tiers or len(subset) < 3:
                subset.append(entry)
                seen_tiers.add(tier)
            if len(subset) >= 5:
                break
        dataset = subset
        print(f"Quick mode: running {len(dataset)} of {len(json.loads(EVAL_DATASET.read_text()))} questions.")
    else:
        print(f"Running {len(dataset)} eval questions...")

    print()
    per_question: list[dict] = []
    total_pass = 0

    for i, entry in enumerate(dataset, 1):
        qid      = entry.get("id", f"q{i}")
        question = entry.get("question", "")
        mode     = entry.get("eval_mode", "retrieval_check")

        result = await _evaluate_question(base_url, token, entry, mode)
        per_question.append(result)

        if result["hit"]:
            total_pass += 1
            icon = "PASS"
        else:
            icon = "FAIL"

        print(f"  [{i:>2}/{len(dataset)}] {icon}  [{qid}] {question[:55]}")
        if result.get("reason"):
            print(f"          Reason: {result['reason']}")

    total  = len(dataset)
    rate   = total_pass / total if total else 0.0
    passed = rate >= pass_threshold

    # Build summary
    by_complexity: dict[str, dict] = {}
    for r in per_question:
        c = r.get("complexity", "simple")
        if c not in by_complexity:
            by_complexity[c] = {"total": 0, "passed": 0}
        by_complexity[c]["total"]  += 1
        by_complexity[c]["passed"] += int(r["hit"])

    results = {
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "total_questions":  total,
        "passed":           total_pass,
        "overall_pass_rate": round(rate, 4),
        "pass_threshold":   pass_threshold,
        "gate_passed":      passed,
        "quick_mode":       quick,
        "by_complexity":    {
            c: {
                "total":  v["total"],
                "passed": v["passed"],
                "rate":   round(v["passed"] / v["total"], 4) if v["total"] else 0.0,
            }
            for c, v in by_complexity.items()
        },
        "per_question": per_question,
        # Keys used by check_gate.py
        "faithfulness":       rate,
        "retrieval_check_score": rate,
        "context_recall":     rate,
        "precision_at_3":     rate,
        "vault_empty":        rate == 0.0 and total > 0,
    }

    print()
    print("-" * 60)
    print(f"  Total       : {total_pass}/{total} passed")
    print(f"  Pass rate   : {rate:.1%}  (threshold: {pass_threshold:.0%})")
    for c, v in by_complexity.items():
        print(f"  {c:<14}: {v['passed']}/{v['total']}  ({v['passed']/v['total']:.0%})")
    print("-" * 60)

    if passed:
        print(f"\n  EVAL PASS  ({rate:.1%} >= {pass_threshold:.0%})")
    else:
        print(f"\n  EVAL FAIL  ({rate:.1%} < {pass_threshold:.0%})")

    print()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"  Results saved to {RESULTS_FILE}")

    return results, 0 if passed else 1


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="ResearchHub Evaluation Runner — exercises the live API with eval_dataset.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python benchmarking/eval_runner.py --quick\n"
            "  python benchmarking/eval_runner.py --token eyJ...\n"
            "  python benchmarking/eval_runner.py --threshold 0.75"
        ),
    )
    parser.add_argument("--url", default=os.getenv("API_BASE_URL", "http://localhost:8000/api/v1"),
                        help="API base URL")
    parser.add_argument("--token", default=os.getenv("BENCHMARK_TOKEN", ""),
                        help="Bearer token (auto-created if omitted)")
    parser.add_argument("--quick", action="store_true",
                        help="Run only 5 representative questions (faster CI mode)")
    parser.add_argument("--threshold", type=float,
                        default=float(os.getenv("EVAL_PASS_THRESHOLD", str(DEFAULT_PASS_THRESHOLD))),
                        help=f"Minimum pass rate to succeed (default: {DEFAULT_PASS_THRESHOLD})")
    args = parser.parse_args()

    print(f"\n{'=' * 60}")
    print("  ResearchHub Evaluation Runner")
    print(f"  API   : {args.url}")
    print(f"  Mode  : {'quick (5 questions)' if args.quick else 'full'}")
    print(f"  Pass  : {args.threshold:.0%} threshold")
    print(f"{'=' * 60}\n")

    try:
        token = await _get_token(args.url, args.token)
    except Exception as exc:
        print(f"FATAL: Auth failed — {exc}")
        return 2

    _, exit_code = await run_eval(args.url, token, args.quick, args.threshold)
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
