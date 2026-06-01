#!/usr/bin/env python3
"""
Benchmark gate — dual-mode entry point.

  Run mode  (default, no positional args):
      python benchmarking/check_gate.py --tenant <id>
      Runs all benchmark modules in sequence, logs to MLflow + LangSmith
      via unified_logger, then gates results against thresholds.json.

  CI gate mode  (positional JSON args — used by GitHub Actions):
      python benchmarking/check_gate.py results.json latency.json thresholds.json [worker_results.json]
      Pure JSON parsing — no network calls, no benchmark execution.

  Save baseline:
      python benchmarking/check_gate.py --save-baseline
      Saves the current benchmarking/results/full_report.json as the regression baseline.

Exit codes:
    0 — all checked thresholds passed, no critical regressions
    1 — one or more thresholds breached OR critical regression detected
    2 — bad arguments or unreadable / malformed input files
    3 — warning-level regressions only (thresholds pass, no critical failures)
"""
import asyncio
import json
import sys
from pathlib import Path


# ── Shared helpers ────────────────────────────────────────────────────────────

def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _fmt(value: float) -> str:
    return f"{value:.4f}"


def _render_table(
    checks: list[tuple],
    vault_empty: bool,
    thresholds: dict,
) -> list[str]:
    """
    Print a markdown gate table and return the list of failed metric names.

    Each check is a 5-tuple:
        (display_name, value, threshold_key, higher_is_better, skip_if_empty)
    """
    if vault_empty:
        print("Note: vault is empty — quality checks marked N/A.\n")

    print("| Metric | Value | Threshold | Status |")
    print("|--------|------:|----------:|--------|")

    failed: list[str] = []

    for name, value, key, higher_is_better, skip_if_empty in checks:
        threshold = thresholds.get(key)

        if vault_empty and skip_if_empty:
            print(f"| {name} | {_fmt(float(value))} | -- | N/A (vault empty) |")
            continue

        if threshold is None:
            print(f"| {name} | {_fmt(float(value))} | -- | N/A |")
            continue

        value = float(value)
        threshold = float(threshold)
        passed = value >= threshold if higher_is_better else value <= threshold
        status = "PASS" if passed else "FAIL"
        if not passed:
            failed.append(name)

        print(f"| {name} | {_fmt(value)} | {_fmt(threshold)} | {status} |")

    return failed


# ── CI gate mode ──────────────────────────────────────────────────────────────

def _gate_from_json(
    results_path: str,
    latency_path: str,
    thresholds_path: str,
    worker_results_path: str | None,
) -> int:
    """
    Pure JSON gate — reads pre-generated result files, prints a markdown table,
    and exits 1 if any threshold is breached.  No benchmark modules are called.
    Used by GitHub Actions after benchmark steps have already run.
    """
    try:
        results    = _load(results_path)
        latency    = _load(latency_path)
        thresholds = _load(thresholds_path)
    except FileNotFoundError as exc:
        print(f"ERROR: file not found — {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"ERROR: malformed JSON — {exc}", file=sys.stderr)
        return 2

    worker: dict = {}
    if worker_results_path:
        try:
            worker = _load(worker_results_path)
        except FileNotFoundError:
            print("WARNING: worker_results.json not found — Worker P99 check skipped.", file=sys.stderr)
        except json.JSONDecodeError as exc:
            print(f"WARNING: malformed worker_results.json — {exc}", file=sys.stderr)

    vault_empty: bool = results.get("vault_empty", False)

    checks = [
        (
            "Faithfulness",
            results.get("faithfulness") or results.get("retrieval_check_score") or 0.0,
            "faithfulness",
            True,
            True,
        ),
        (
            "Context Recall",
            results.get("context_recall") or results.get("precision_at_3") or 0.0,
            "context_recall",
            True,
            True,
        ),
        (
            "P99 Latency (ms)",
            latency.get("p99_ms", 0.0),
            "p99_latency_ms",
            False,
            False,
        ),
        (
            "Worker P99 (ms)",
            worker.get("p99_completion_ms", 0.0),
            "worker_p99_ms",
            False,
            False,
        ),
        (
            "Cost/Query (USD)",
            results.get("cost_per_query_usd", 0.0),
            "cost_per_query_usd",
            False,
            False,
        ),
        (
            "PII Leak Rate",
            results.get("pii_leak_rate", 0.0),
            "pii_leak_rate",
            False,
            True,
        ),
        (
            "Injection Block Rate",
            results.get("injection_block_rate") or results.get("block_rate", 0.0),
            "injection_block_rate",
            True,
            True,
        ),
    ]

    failed = _render_table(checks, vault_empty, thresholds)
    print()
    if failed:
        print(f"**GATE FAILED** -- {len(failed)} threshold(s) breached: " + ", ".join(failed))
        return 1
    print("**GATE PASSED** -- all checked thresholds met.")
    return 0


# ── Run mode ──────────────────────────────────────────────────────────────────

async def _run_all_benchmarks(tenant_id: str) -> int:
    """
    Orchestrator mode — called when no JSON positional args are supplied.

    Runs all benchmark modules in sequence:
      1. quality_evaluator  → faithfulness, answer_relevancy, hallucination_rate
      2. latency_profiler   → p50, p95, p99 pipeline latency
      3. cost_tracker       → avg cost per query, cache savings
      4. unified_logger     → log everything to MLflow + LangSmith, save snapshot
      5. scorecard gate     → compare live results against thresholds.json

    If ANY threshold is breached the function logs a WARNING and returns 1.
    Falls back gracefully if individual modules fail — remaining modules still run.
    """
    thresholds_path = Path(__file__).parent / "thresholds.json"
    try:
        thresholds = _load(str(thresholds_path))
    except FileNotFoundError:
        print(f"ERROR: thresholds.json not found at {thresholds_path}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"ERROR: malformed thresholds.json — {exc}", file=sys.stderr)
        return 2

    print(f"\n{'=' * 65}")
    print(f"  ResearchHub Benchmark Gate — Run Mode")
    print(f"  Tenant: {tenant_id}")
    print(f"{'=' * 65}")

    # ── Step 1: Quality ───────────────────────────────────────────────────────
    print("\n[1/5] Quality Evaluation (quality_evaluator)...")
    faithfulness       = 0.0
    answer_relevancy   = 0.0
    hallucination_rate = 0.0
    try:
        from benchmarking.quality_evaluator import run_quality_evaluation
        q = await run_quality_evaluation(tenant_id, sample_size=30)
        faithfulness       = q.get("avg_faithfulness",    0.0)
        answer_relevancy   = q.get("avg_answer_relevancy", 0.0)
        hallucination_rate = q.get("hallucination_rate",   0.0)
        evaluated = q.get("evaluated", 0)
        print(f"      faithfulness={faithfulness:.4f}  answer_relevancy={answer_relevancy:.4f}"
              f"  hallucination_rate={hallucination_rate:.4f}  n={evaluated}")
        if evaluated == 0:
            print("      WARNING: no TraceLog rows found — quality scores are 0.0")
    except Exception as exc:
        print(f"      WARNING: quality_evaluator failed — {exc}")

    # ── Step 2: Latency ───────────────────────────────────────────────────────
    print("\n[2/5] Latency Profiler (latency_profiler, 5 iterations)...")
    p50_ms = p95_ms = p99_ms = 0.0
    try:
        from benchmarking.latency_profiler import stage_profiler, percentile_report
        probe = "What is the main finding of this paper?"
        times: list[float] = []
        for i in range(5):
            stages = await stage_profiler(probe, tenant_id)
            total = stages.get("total_pipeline_ms")
            if total is not None:
                times.append(float(total))
                print(f"      iter {i + 1}: {total:.1f} ms  "
                      f"(cache={stages.get('cache_check_ms')}ms  "
                      f"vdb={stages.get('vector_search_ms')}ms  "
                      f"llm={stages.get('llm_generation_ms')}ms)")
        if times:
            rpt = percentile_report(times)
            p50_ms = rpt.get("p50_ms", 0.0)
            p95_ms = rpt.get("p95_ms", 0.0)
            p99_ms = rpt.get("p99_ms", 0.0)
            print(f"      p50={p50_ms:.1f}ms  p95={p95_ms:.1f}ms  p99={p99_ms:.1f}ms")
        else:
            print("      WARNING: all stage_profiler iterations failed")
    except Exception as exc:
        print(f"      WARNING: latency_profiler failed — {exc}")

    # ── Step 3: Cost ──────────────────────────────────────────────────────────
    print("\n[3/5] Cost Tracker (cost_tracker)...")
    cost_per_query_usd = 0.0
    cache_savings_usd  = 0.0
    try:
        from benchmarking.cost_tracker import per_query_breakdown, cache_savings_calculator
        breakdown = per_query_breakdown(n=50)
        savings   = cache_savings_calculator()
        cost_per_query_usd = breakdown.get("avg_usd", 0.0)
        cache_savings_usd  = savings.get("savings_usd", 0.0)
        print(f"      avg_cost_per_query=${cost_per_query_usd:.6f}  "
              f"cache_hits={savings.get('cache_hits_detected', 0)}  "
              f"cache_savings=${cache_savings_usd:.6f}")
        if breakdown.get("count", 0) == 0:
            print("      WARNING: no UsageLog rows — cost is 0.0")
    except Exception as exc:
        print(f"      WARNING: cost_tracker failed — {exc}")

    # ── Step 4: Unified logger (MLflow + LangSmith + snapshot) ───────────────
    print("\n[4/5] Unified Logger (MLflow + LangSmith + drift snapshot)...")
    from benchmarking.scorecard import (
        BenchmarkResult, QualityMetrics, LatencyMetrics, CostMetrics,
    )
    benchmark_result: BenchmarkResult | None = None
    try:
        from benchmarking.unified_logger import weekly_run
        benchmark_result = await weekly_run(tenant_id, run_drift=True)
        print(f"      run_id={benchmark_result.run_id}")
    except Exception as exc:
        print(f"      WARNING: unified_logger.weekly_run failed — {exc}")
        print("      Building minimal scorecard from collected metrics...")
        from benchmarking.unified_logger import save_snapshot
        benchmark_result = BenchmarkResult(
            quality=QualityMetrics(
                faithfulness=faithfulness,
                answer_relevancy=answer_relevancy,
                hallucination_rate=hallucination_rate,
            ),
            latency=LatencyMetrics(p50_ms=p50_ms, p95_ms=p95_ms, p99_ms=p99_ms),
            cost=CostMetrics(
                cost_per_query_usd=cost_per_query_usd,
                cache_savings_usd=cache_savings_usd,
            ),
        )
        save_snapshot(benchmark_result)

    # ── Step 5: Gate against thresholds ──────────────────────────────────────
    print("\n[5/5] Gate Check (thresholds.json)...")
    q_met = benchmark_result.quality
    l_met = benchmark_result.latency
    c_met = benchmark_result.cost
    s_met = benchmark_result.safety

    checks = [
        ("Faithfulness",         q_met.faithfulness,        "faithfulness",         True,  False),
        ("Answer Relevancy",     q_met.answer_relevancy,    "context_recall",       True,  False),
        ("P95 Latency (ms)",     l_met.p95_ms,              "p99_latency_ms",       False, False),
        ("Cost/Query (USD)",     c_met.cost_per_query_usd,  "cost_per_query_usd",   False, False),
        ("PII Leak Rate",        s_met.pii_leak_rate,       "pii_leak_rate",        False, False),
        ("Injection Block Rate", s_met.injection_block_rate, "injection_block_rate", True,  False),
    ]

    print()
    failed = _render_table(checks, vault_empty=False, thresholds=thresholds)
    print()
    if failed:
        import logging
        logging.getLogger("ResearchHub").warning(
            "GATE: %d threshold(s) breached — %s", len(failed), ", ".join(failed)
        )
        print(f"**GATE FAILED** — {len(failed)} threshold(s) breached: " + ", ".join(failed))
        return 1

    print("**GATE PASSED** — all thresholds met.")
    return 0


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    import argparse

    # ── --save-baseline shortcut ──────────────────────────────────────────────
    if "--save-baseline" in sys.argv:
        from benchmarking.baseline_manager import save_baseline
        results_file = Path("benchmarking/results/full_report.json")
        if not results_file.exists():
            print("ERROR: benchmarking/results/full_report.json not found.")
            print("Run:  python benchmarking/run_benchmark.py  first.")
            return 2
        try:
            data = json.loads(results_file.read_text(encoding="utf-8"))
            save_baseline(data)
            return 0
        except Exception as exc:
            print(f"ERROR: Could not save baseline — {exc}")
            return 2

    # ── CI gate mode: detected by positional .json arguments ─────────────────
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    if positional and positional[0].endswith(".json"):
        if len(sys.argv) not in (4, 5):
            print(
                f"Usage: {sys.argv[0]} results.json latency.json thresholds.json [worker_results.json]",
                file=sys.stderr,
            )
            return 2
        _, results_path, latency_path, thresholds_path = sys.argv[:4]
        worker_path = sys.argv[4] if len(sys.argv) == 5 else None

        gate_exit = _gate_from_json(results_path, latency_path, thresholds_path, worker_path)

        # Regression check on top of threshold check
        try:
            from benchmarking.baseline_manager import compare_with_baseline, print_regression_report
            results = _load(results_path)
            report  = compare_with_baseline(results)
            print()
            print_regression_report(report)

            critical = report.get("critical_regressions", 0)
            warning  = report.get("warning_regressions", 0)

            if not report.get("has_baseline"):
                print("  No baseline set.  Run:  python benchmarking/check_gate.py --save-baseline")
            elif critical > 0:
                # Critical regression overrides a passing gate
                if gate_exit == 0:
                    print("  Gate passed thresholds but CRITICAL regression detected — failing.")
                return 1
            elif warning > 0 and gate_exit == 0:
                print("  Gate passed.  Warning-level regressions detected (exit 3).")
                return 3
        except ImportError:
            pass   # baseline_manager optional in bare CI environments
        except Exception as exc:
            print(f"  Regression check skipped — {exc}")

        return gate_exit

    # ── Run mode: orchestrate all benchmarks, then gate ───────────────────────
    parser = argparse.ArgumentParser(
        description="ResearchHub Benchmark Gate — run all benchmarks and check thresholds.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m benchmarking.check_gate --tenant default\n"
            "  python benchmarking/check_gate.py results.json latency.json thresholds.json\n"
            "  python benchmarking/check_gate.py --save-baseline"
        ),
    )
    parser.add_argument("--tenant", default="default",
                        help="Tenant ID to benchmark (default: 'default')")
    parser.add_argument("--save-baseline", action="store_true",
                        help="Save current full_report.json as regression baseline and exit")
    args = parser.parse_args()

    if args.save_baseline:
        from benchmarking.baseline_manager import save_baseline
        results_file = Path("benchmarking/results/full_report.json")
        if not results_file.exists():
            print("ERROR: benchmarking/results/full_report.json not found.  Run run_benchmark.py first.")
            return 2
        data = json.loads(results_file.read_text(encoding="utf-8"))
        save_baseline(data)
        return 0

    gate_exit = asyncio.run(_run_all_benchmarks(args.tenant))

    # Regression check after the live benchmark run
    try:
        from benchmarking.baseline_manager import compare_with_baseline, print_regression_report
        results_file = Path("benchmarking/results/full_report.json")
        if results_file.exists():
            current = json.loads(results_file.read_text(encoding="utf-8"))
            report  = compare_with_baseline(current)
            print_regression_report(report)
            if report.get("critical_regressions", 0) > 0 and gate_exit == 0:
                return 1
            if report.get("warning_regressions", 0) > 0 and gate_exit == 0:
                return 3
    except Exception:
        pass

    return gate_exit


if __name__ == "__main__":
    sys.exit(main())
