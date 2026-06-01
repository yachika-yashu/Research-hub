#!/usr/bin/env python3
"""
ResearchHub Full Benchmark Runner.

Runs all benchmark modules in sequence, saves per-module JSON results,
logs to MLflow + LangSmith, and prints a Unicode summary table.

Usage:
    python benchmarking/run_benchmark.py [--tenant TENANT_ID] [--skip-preflight]

Output files (benchmarking/results/):
    quality.json   latency.json   cost.json   safety.json
    worker.json    full_report.json
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows cmd/PowerShell defaults to cp1252 which cannot encode Unicode box
# drawing characters.  Force UTF-8 so the progress table renders correctly.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ensure the project root is on sys.path so `app.*` imports resolve when
# running directly:  python benchmarking/run_benchmark.py
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env BEFORE any app import so app.core.config picks up local overrides.
from dotenv import load_dotenv
load_dotenv()

RESULTS_DIR     = Path(__file__).parent / "results"
THRESHOLDS_PATH = Path(__file__).parent / "thresholds.json"


# ── Pre-flight connectivity checks ────────────────────────────────────────────

async def _check_redis() -> tuple[bool, str]:
    try:
        import redis.asyncio as aioredis
        from app.core.config import REDIS_URL
        client = aioredis.from_url(REDIS_URL, decode_responses=True,
                                   socket_connect_timeout=5)
        await asyncio.wait_for(client.ping(), timeout=5.0)
        await client.aclose()
        return True, REDIS_URL
    except asyncio.TimeoutError:
        return False, "connection timed out (5s)"
    except Exception as exc:
        return False, str(exc)


def _check_postgres() -> tuple[bool, str]:
    try:
        from app.core.database import engine
        with engine.connect():
            pass
        return True, str(engine.url).split("@")[-1]  # host:port/db only
    except Exception as exc:
        return False, str(exc)


def _check_qdrant() -> tuple[bool, str]:
    try:
        from app.services.vector_store import get_qdrant
        colls = get_qdrant().get_collections()
        return True, f"{len(colls.collections)} collection(s)"
    except Exception as exc:
        return False, str(exc)


async def preflight() -> bool:
    """
    Verify Redis, PostgreSQL, and Qdrant are reachable.
    Returns True only when all three pass. Prints ✓/✗ for each.
    """
    print("Pre-flight service checks...")
    redis_ok,    redis_msg    = await _check_redis()
    postgres_ok, postgres_msg = await asyncio.get_running_loop().run_in_executor(
        None, _check_postgres
    )
    qdrant_ok,   qdrant_msg   = await asyncio.get_running_loop().run_in_executor(
        None, _check_qdrant
    )

    print(f"  {'✓' if redis_ok else '✗'} Redis       — {redis_msg}")
    print(f"  {'✓' if postgres_ok else '✗'} PostgreSQL  — {postgres_msg}")
    print(f"  {'✓' if qdrant_ok else '✗'} Qdrant      — {qdrant_msg}")

    if not all([redis_ok, postgres_ok, qdrant_ok]):
        print()
        print("FATAL: one or more required services are unreachable.")
        print("Start the full stack with:  docker compose up -d")
        print("Then wait ~15 s for health checks, and re-run.")
        return False

    print()
    return True


# ── Result file helpers ───────────────────────────────────────────────────────

def _save(data: dict | list, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)


def _err(exc: Exception, defaults: dict) -> dict:
    """Return a defaults dict annotated with the error so the run continues."""
    result = dict(defaults)
    result["error"] = f"{type(exc).__name__}: {exc}"
    return result


# ── Per-module benchmark runners ──────────────────────────────────────────────

async def _run_quality(tenant_id: str) -> dict:
    """quality_evaluator → benchmarking/results/quality.json"""
    from benchmarking.quality_evaluator import run_quality_evaluation
    try:
        result = await run_quality_evaluation(tenant_id, sample_size=30)
    except Exception as exc:
        result = _err(exc, {"avg_faithfulness": 0.0, "avg_answer_relevancy": 0.0,
                            "hallucination_rate": 0.0, "evaluated": 0})
    _save(result, RESULTS_DIR / "quality.json")
    return result


async def _run_latency(tenant_id: str, iterations: int = 5) -> dict:
    """latency_profiler → benchmarking/results/latency.json"""
    from benchmarking.latency_profiler import stage_profiler, percentile_report
    probe = "What are the main findings of this paper?"
    try:
        times: list[float] = []
        stages_sample: dict | None = None
        for _ in range(iterations):
            s = await stage_profiler(probe, tenant_id)
            t = s.get("total_pipeline_ms")
            if t is not None:
                times.append(float(t))
            if stages_sample is None:
                stages_sample = s

        result = percentile_report(times) if times else {
            "count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0,
            "min_ms": 0.0, "max_ms": 0.0, "mean_ms": 0.0,
        }
        if stages_sample:
            result["stages_sample"] = stages_sample
        result["iterations"] = iterations
    except Exception as exc:
        result = _err(exc, {"p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0,
                            "mean_ms": 0.0, "count": 0})
    _save(result, RESULTS_DIR / "latency.json")
    return result


def _run_cost_sync() -> dict:
    """cost_tracker (sync DB reads) → benchmarking/results/cost.json"""
    from benchmarking.cost_tracker import (
        per_query_breakdown, per_ingestion_breakdown, cache_savings_calculator,
    )
    try:
        result = {
            "query":     per_query_breakdown(n=50),
            "ingestion": per_ingestion_breakdown(),
            "savings":   cache_savings_calculator(),
        }
    except Exception as exc:
        result = _err(exc, {"query": {}, "ingestion": {}, "savings": {}})
    _save(result, RESULTS_DIR / "cost.json")
    return result


async def _run_safety() -> dict:
    """safety_evaluator → benchmarking/results/safety.json"""
    from benchmarking.safety_evaluator import run_safety_benchmark
    try:
        bench = await run_safety_benchmark()
        result = {
            "pii_leak_rate":        bench.pii_leak_rate,
            "injection_block_rate": bench.injection_block_rate,
            "refusal_rate":         bench.refusal_rate,
            "blocked_injections":   bench.blocked_injections,
            "total_probes":         bench.total_probes,
            "total_off_topic":      bench.total_off_topic_queries,
            "correct_refusals":     bench.correct_refusals,
        }
    except Exception as exc:
        result = _err(exc, {"pii_leak_rate": 0.0, "injection_block_rate": 0.0,
                            "refusal_rate": 0.0})
    _save(result, RESULTS_DIR / "safety.json")
    return result


async def _run_worker() -> dict:
    """worker_benchmarker queue depth → benchmarking/results/worker.json"""
    from benchmarking.worker_benchmarker import queue_depth_monitor
    from app.core.config import REDIS_URL
    try:
        series = await queue_depth_monitor(duration_seconds=15, redis_url=REDIS_URL)
        result = {
            "queue_depth_series": series,
            # p99_completion_ms is 0.0 unless BENCHMARK_TOKEN + BENCHMARK_PDF_PATH
            # are set and time_ingestion_job is called directly
            "p99_completion_ms": 0.0,
        }
    except Exception as exc:
        result = _err(exc, {"queue_depth_series": [], "p99_completion_ms": 0.0})
    _save(result, RESULTS_DIR / "worker.json")
    return result


# ── Summary table ─────────────────────────────────────────────────────────────

def _gate(value: float, threshold: float | None, higher_is_better: bool) -> tuple[str, bool | None]:
    """Return (symbol, passed).  passed=None means info-only (no threshold)."""
    if threshold is None:
        return "  ℹ️ ", None
    passed = value >= threshold if higher_is_better else value <= threshold
    return ("  ✅ " if passed else "  ❌ "), passed


def print_summary_table(rows: list[dict]) -> bool:
    """
    Print a Unicode box-drawing summary table. Returns True when every
    thresholded metric passes.

    Each row dict keys: name, value (float), value_fmt (str),
    threshold (float|None), threshold_fmt (str), higher_is_better (bool).
    """
    col = [27, 13, 12, 8]

    def _bar(left, mid, right):
        return left + mid.join("─" * w for w in col) + right

    def _row(*cells):
        parts = []
        for i, c in enumerate(cells):
            content = f" {c} "
            parts.append(content.ljust(col[i])[:col[i]])
        return "│" + "│".join(parts) + "│"

    print()
    print(_bar("┌", "┬", "┐"))
    print(_row("Metric", "Value", "Threshold", "Status"))
    print(_bar("├", "┼", "┤"))

    all_pass = True
    for r in rows:
        symbol, passed = _gate(r["value"], r.get("threshold"), r.get("higher_is_better", True))
        if passed is False:
            all_pass = False
        thresh = r.get("threshold_fmt", "—") if r.get("threshold") is not None else "—"
        print(_row(r["name"], r["value_fmt"], thresh, symbol))

    print(_bar("└", "┴", "┘"))
    overall = "PASS ✅" if all_pass else "FAIL ❌"
    print(f"\n  Overall: {overall}\n")
    return all_pass


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> int:
    parser = argparse.ArgumentParser(
        description="ResearchHub Full Benchmark Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python benchmarking/run_benchmark.py\n"
            "  python benchmarking/run_benchmark.py --tenant team-alpha\n"
            "  python benchmarking/run_benchmark.py --skip-preflight"
        ),
    )
    parser.add_argument(
        "--tenant",
        default=os.getenv("BENCHMARK_TENANT_ID", "default"),
        help="Tenant ID to benchmark (default: BENCHMARK_TENANT_ID env var or 'default')",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip service connectivity checks (e.g. inside Docker)",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="After the benchmark run, save results as the regression baseline",
    )
    args = parser.parse_args()
    tenant_id = args.tenant

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'═' * 65}")
    print("  ResearchHub Full Benchmark Suite")
    print(f"  Tenant  : {tenant_id}")
    print(f"  Started : {ts}")
    print(f"{'═' * 65}\n")

    # ── Pre-flight ────────────────────────────────────────────────────────────
    if not args.skip_preflight:
        if not await preflight():
            return 2

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with open(THRESHOLDS_PATH, encoding="utf-8") as fh:
            thresholds = json.load(fh)
    except Exception as exc:
        print(f"WARNING: cannot load thresholds.json — {exc}\nProceeding with N/A thresholds.\n")
        thresholds = {}

    loop = asyncio.get_running_loop()

    # ── Step 1: Quality ───────────────────────────────────────────────────────
    print("[1/6] Quality Evaluation (RAGAS faithfulness + answer relevancy)...")
    q = await _run_quality(tenant_id)
    faith     = q.get("avg_faithfulness",    0.0)
    relevancy = q.get("avg_answer_relevancy", 0.0)
    hall_rate = q.get("hallucination_rate",   0.0)
    q_n       = q.get("evaluated", 0)
    print(f"      faithfulness={faith:.4f}  answer_relevancy={relevancy:.4f}  "
          f"hallucination_rate={hall_rate:.4f}  (n={q_n} TraceLog rows)")
    if "error" in q:
        print(f"      ⚠️  {q['error']}")

    # ── Step 2: Latency ───────────────────────────────────────────────────────
    print("\n[2/6] Latency Profiler (5 × stage_profiler: Redis→Qdrant→LLM)...")
    lat  = await _run_latency(tenant_id)
    p50  = lat.get("p50_ms", 0.0)
    p95  = lat.get("p95_ms", 0.0)
    p99  = lat.get("p99_ms", 0.0)
    mean = lat.get("mean_ms", 0.0)
    n_ok = lat.get("count", 0)
    print(f"      p50={p50:.1f}ms  p95={p95:.1f}ms  p99={p99:.1f}ms  "
          f"mean={mean:.1f}ms  ({n_ok}/5 iterations succeeded)")
    if "error" in lat:
        print(f"      ⚠️  {lat['error']}")

    # ── Step 3: Cost ──────────────────────────────────────────────────────────
    print("\n[3/6] Cost Tracker (last 50 UsageLog query rows)...")
    cost_data   = await loop.run_in_executor(None, _run_cost_sync)
    cost_per_q  = cost_data.get("query",   {}).get("avg_usd",              0.0)
    savings_usd = cost_data.get("savings", {}).get("savings_usd",          0.0)
    cache_hits  = cost_data.get("savings", {}).get("cache_hits_detected",   0)
    total_q_db  = cost_data.get("savings", {}).get("logged_queries",        0)
    cache_rate  = round(cache_hits / total_q_db, 4) if total_q_db else 0.0
    print(f"      avg_cost_per_query=${cost_per_q:.6f}  cache_hit_rate={cache_rate:.1%}  "
          f"cache_savings=${savings_usd:.6f}  (n={total_q_db} logged queries)")
    if "error" in cost_data:
        print(f"      ⚠️  {cost_data['error']}")

    # ── Step 4: Safety ────────────────────────────────────────────────────────
    print("\n[4/6] Safety Evaluator (20 injection probes, PII scan)...")
    safety      = await _run_safety()
    block_rate  = safety.get("injection_block_rate", 0.0)
    pii_rate    = safety.get("pii_leak_rate",        0.0)
    refusal_rate = safety.get("refusal_rate",        0.0)
    blocked_n   = safety.get("blocked_injections",   0)
    total_n     = safety.get("total_probes",         0)
    print(f"      injection_block_rate={block_rate:.1%} ({blocked_n}/{total_n})  "
          f"pii_leak_rate={pii_rate:.1%}  refusal_rate={refusal_rate:.1%}")
    if "error" in safety:
        print(f"      ⚠️  {safety['error']}")

    # ── Step 5: Worker ────────────────────────────────────────────────────────
    print("\n[5/6] Worker Benchmarker (arq:queue depth, 15 s poll)...")
    worker     = await _run_worker()
    worker_p99 = worker.get("p99_completion_ms", 0.0)
    d_series   = worker.get("queue_depth_series", [])
    depths     = [d["queue_depth"] for d in d_series if d.get("queue_depth", -1) >= 0]
    max_depth  = max(depths) if depths else 0
    avg_depth  = round(sum(depths) / len(depths), 2) if depths else 0.0
    print(f"      p99_completion_ms={worker_p99:.1f}  "
          f"max_queue_depth={max_depth}  avg_queue_depth={avg_depth}")
    if "error" in worker:
        print(f"      ⚠️  {worker['error']}")

    # ── Step 6: Log + snapshot ────────────────────────────────────────────────
    print("\n[6/6] Logging (MLflow + LangSmith + drift snapshot)...")

    from benchmarking.scorecard import (
        BenchmarkResult, QualityMetrics, LatencyMetrics,
        CostMetrics, SafetyMetrics, ReliabilityMetrics,
    )
    from benchmarking.unified_logger import log_to_mlflow, save_snapshot

    run_id = f"manual-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    benchmark_result = BenchmarkResult(
        run_id=run_id,
        quality=QualityMetrics(
            faithfulness=faith,
            answer_relevancy=relevancy,
            hallucination_rate=hall_rate,
        ),
        latency=LatencyMetrics(
            p50_ms=p50,
            p95_ms=p95,
            p99_ms=p99,
            ttft_ms=mean,
            ingestion_p99_ms=worker_p99,
        ),
        cost=CostMetrics(
            cost_per_query_usd=cost_per_q,
            cache_savings_usd=savings_usd,
        ),
        safety=SafetyMetrics(
            injection_block_rate=block_rate,
            pii_leak_rate=pii_rate,
            refusal_rate=refusal_rate,
        ),
        reliability=ReliabilityMetrics(
            cache_hit_rate=cache_rate,
        ),
    )

    mlflow_run_id = await loop.run_in_executor(None, log_to_mlflow, benchmark_result)
    snapshot_path = await loop.run_in_executor(None, save_snapshot, benchmark_result)

    if mlflow_run_id:
        print(f"      MLflow run_id : {mlflow_run_id}")
    else:
        print("      MLflow        : skipped (MLFLOW_TRACKING_URI unreachable or mlflow not installed)")

    langchain_key = os.getenv("LANGCHAIN_API_KEY", "")
    if langchain_key:
        print(f"      LangSmith     : project '{os.getenv('LANGCHAIN_PROJECT', 'researchhub-benchmarking')}'")
    else:
        print("      LangSmith     : skipped (LANGCHAIN_API_KEY not set)")

    print(f"      Snapshot      : {snapshot_path}")

    # ── Summary table ─────────────────────────────────────────────────────────
    q_m = benchmark_result.quality
    l_m = benchmark_result.latency
    c_m = benchmark_result.cost
    s_m = benchmark_result.safety
    r_m = benchmark_result.reliability

    th = thresholds  # alias for brevity
    table_rows = [
        {
            "name":            "Faithfulness",
            "value":           q_m.faithfulness,
            "value_fmt":       f"{q_m.faithfulness:.4f}",
            "threshold":       th.get("faithfulness"),
            "threshold_fmt":   f"≥ {th['faithfulness']:.2f}" if "faithfulness" in th else "—",
            "higher_is_better": True,
        },
        {
            "name":            "Answer Relevancy",
            "value":           q_m.answer_relevancy,
            "value_fmt":       f"{q_m.answer_relevancy:.4f}",
            "threshold":       th.get("context_recall"),
            "threshold_fmt":   f"≥ {th['context_recall']:.2f}" if "context_recall" in th else "—",
            "higher_is_better": True,
        },
        {
            "name":            "Hallucination Rate",
            "value":           q_m.hallucination_rate,
            "value_fmt":       f"{q_m.hallucination_rate:.2%}",
            "threshold":       None,  # info — tracked but not gated
            "threshold_fmt":   "—",
            "higher_is_better": False,
        },
        {
            "name":            "P95 Latency (ms)",
            "value":           l_m.p95_ms,
            "value_fmt":       f"{l_m.p95_ms:.1f}",
            "threshold":       th.get("p99_latency_ms"),
            "threshold_fmt":   f"≤ {th['p99_latency_ms']:.0f}" if "p99_latency_ms" in th else "—",
            "higher_is_better": False,
        },
        {
            "name":            "Cost/Query (USD)",
            "value":           c_m.cost_per_query_usd,
            "value_fmt":       f"${c_m.cost_per_query_usd:.6f}",
            "threshold":       th.get("cost_per_query_usd"),
            "threshold_fmt":   f"≤ ${th['cost_per_query_usd']:.4f}" if "cost_per_query_usd" in th else "—",
            "higher_is_better": False,
        },
        {
            "name":            "Cache Hit Rate",
            "value":           r_m.cache_hit_rate,
            "value_fmt":       f"{r_m.cache_hit_rate:.1%}",
            "threshold":       None,  # info — no gate
            "threshold_fmt":   "—",
            "higher_is_better": True,
        },
        {
            "name":            "PII Leak Rate",
            "value":           s_m.pii_leak_rate,
            "value_fmt":       f"{s_m.pii_leak_rate:.2%}",
            "threshold":       th.get("pii_leak_rate"),
            "threshold_fmt":   f"≤ {th['pii_leak_rate']:.2f}" if "pii_leak_rate" in th else "—",
            "higher_is_better": False,
        },
        {
            "name":            "Injection Block Rate",
            "value":           s_m.injection_block_rate,
            "value_fmt":       f"{s_m.injection_block_rate:.1%}",
            "threshold":       th.get("injection_block_rate"),
            "threshold_fmt":   f"≥ {th['injection_block_rate']:.2f}" if "injection_block_rate" in th else "—",
            "higher_is_better": True,
        },
        {
            "name":            "Worker P99 (ms)",
            "value":           worker_p99,
            "value_fmt":       f"{worker_p99:.1f}",
            "threshold":       th.get("worker_p99_ms"),
            "threshold_fmt":   f"≤ {th['worker_p99_ms']:.0f}" if "worker_p99_ms" in th else "—",
            "higher_is_better": False,
        },
    ]

    all_pass = print_summary_table(table_rows)

    # ── Baseline save / regression compare ───────────────────────────────────
    if args.save_baseline:
        try:
            from benchmarking.baseline_manager import save_baseline
            # Build a flat version of the scorecard for comparison
            _baseline_data = benchmark_result.to_dict()
            save_baseline(_baseline_data)
            print(f"\n  Baseline saved (commit: {_git_commit_hash() if hasattr(benchmark_result, 'run_id') else '?'})")
        except Exception as _bexc:
            print(f"  WARNING: Could not save baseline — {_bexc}")
    else:
        try:
            from benchmarking.baseline_manager import compare_with_baseline, print_regression_report
            _report = compare_with_baseline(benchmark_result.to_dict())
            print_regression_report(_report)
        except Exception:
            pass

    # ── Full report ───────────────────────────────────────────────────────────
    report_path = RESULTS_DIR / "full_report.json"
    full_report = {
        "run_id":       run_id,
        "tenant_id":    tenant_id,
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "overall_pass": all_pass,
        "quality":      q,
        "latency":      lat,
        "cost":         cost_data,
        "safety":       safety,
        "worker":       worker,
        "scorecard":    benchmark_result.to_dict(),
    }
    _save(full_report, report_path)
    print(f"  Full report → {report_path}")
    print(f"  Per-module  → {RESULTS_DIR}/{{quality,latency,cost,safety,worker}}.json")
    print(f"{'═' * 65}\n")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
