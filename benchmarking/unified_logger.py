"""
Unified benchmark logger — extends the existing app logger, logs all five
BenchmarkResult dimensions to MLflow, tags runs with the current git commit,
generates an Evidently data-drift report, and orchestrates a full weekly run.

Import the existing logger — never replace or reconfigure it.
Runnable standalone: python -m benchmarking.unified_logger
"""
import asyncio
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.core.logging import logger
from benchmarking.scorecard import BenchmarkResult, QualityMetrics, LatencyMetrics, CostMetrics, SafetyMetrics, ReliabilityMetrics


# ── Git helpers ──────────────────────────────────────────────────────────────

def _git_commit_hash() -> str:
    """Return the short HEAD commit hash, or 'unknown' if git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ── MLflow logging ───────────────────────────────────────────────────────────

def log_to_mlflow(result: BenchmarkResult, experiment_name: str = "researchhub") -> Optional[str]:
    """
    Log all BenchmarkResult dimensions as MLflow metrics + tags.

    Returns the MLflow run_id on success, None if MLflow is unavailable.
    The function is deliberately non-blocking on import error so benchmarking
    still works in environments without the mlflow package.
    """
    mlflow_uri = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
    try:
        import mlflow

        mlflow.set_tracking_uri(mlflow_uri)
        mlflow.set_experiment(experiment_name)

        with mlflow.start_run(run_name=result.run_id or "benchmark") as run:
            commit = _git_commit_hash()
            mlflow.set_tags({
                "git_commit": commit,
                "generation_model": result.generation_model,
                "embedding_model": result.embedding_model,
                "timestamp": result.timestamp or "",
            })

            flat = result.to_dict()
            numeric_metrics = {k: v for k, v in flat.items() if isinstance(v, (int, float))}
            mlflow.log_metrics(numeric_metrics)

            run_id = run.info.run_id
            logger.info(f"UNIFIED_LOGGER: MLflow run logged — id={run_id}  commit={commit}")
            return run_id

    except ImportError:
        logger.info("UNIFIED_LOGGER: mlflow not installed — skipping MLflow logging.")
        return None
    except Exception as exc:
        logger.warning(f"UNIFIED_LOGGER: MLflow logging failed — {exc}")
        return None


# ── Evidently drift report ───────────────────────────────────────────────────

def generate_drift_report(
    reference_path: str,
    current_path: str,
    output_path: str = "benchmarking/drift_report.html",
) -> bool:
    """
    Generate an Evidently data-drift HTML report comparing two JSONL snapshot files.

    BUG 5 FIX: the old code called pd.DataFrame(single_dict) which raises
    ValueError because scalar values need an index. Each snapshot file is now
    a JSONL file where every line is one flat benchmark-run dict. Multiple lines
    give Evidently the statistical sample it needs for meaningful drift detection.

    Returns True on success, False if Evidently is unavailable or data is malformed.
    """
    try:
        import pandas as pd
        from evidently.report import Report
        from evidently.metric_preset import DataDriftPreset

        def _read_jsonl(path: str) -> list[dict]:
            rows: list[dict] = []
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
            return rows

        ref_data = _read_jsonl(reference_path)
        cur_data = _read_jsonl(current_path)

        if not ref_data or not cur_data:
            logger.warning("UNIFIED_LOGGER: Drift report skipped — one or both snapshot files are empty.")
            return False

        ref_df = pd.DataFrame(ref_data)
        cur_df = pd.DataFrame(cur_data)

        # Drop string columns that DataDriftPreset cannot analyse numerically
        _drop_cols = ["run_id", "timestamp", "generation_model", "embedding_model"]
        ref_df.drop(columns=[c for c in _drop_cols if c in ref_df.columns], inplace=True)
        cur_df.drop(columns=[c for c in _drop_cols if c in cur_df.columns], inplace=True)

        report = Report(metrics=[DataDriftPreset()])
        report.run(reference_data=ref_df, current_data=cur_df)

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        report.save_html(str(out))
        logger.info(f"UNIFIED_LOGGER: Evidently drift report saved to {output_path}")
        return True

    except ImportError:
        logger.info("UNIFIED_LOGGER: evidently not installed — skipping drift report.")
        return False
    except FileNotFoundError as exc:
        logger.warning(f"UNIFIED_LOGGER: Drift report skipped — {exc}")
        return False
    except Exception as exc:
        logger.warning(f"UNIFIED_LOGGER: Drift report generation failed — {exc}")
        return False


# ── Snapshot persistence ─────────────────────────────────────────────────────

def save_snapshot(result: BenchmarkResult, output_dir: str = "benchmarking/snapshots") -> str:
    """
    Append this run's metrics as one JSON line to history.jsonl.

    BUG 5 FIX: the old code wrote a single dict per timestamped file.
    pd.DataFrame(single_dict) then crashed because all values are scalars.
    Now we use JSONL append mode — one JSON object per line — so the file grows
    across runs and generate_drift_report() can build a proper multi-row DataFrame.

    Returns the path to history.jsonl.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "history.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(result.to_dict()) + "\n")
    logger.info(f"UNIFIED_LOGGER: Snapshot appended to {path}")
    return str(path)


def _load_latest_snapshot(snapshot_dir: str = "benchmarking/snapshots") -> Optional[tuple[str, str]]:
    """
    Split history.jsonl into an older-half reference and a recent-half current file.

    BUG 5 FIX: previously returned the path to the second-most-recent single-dict
    file (which fed a broken single-row DataFrame into Evidently). Now reads the
    growing JSONL, splits it at the midpoint, writes two temp files, and returns
    their paths so generate_drift_report() can build two proper DataFrames.

    Returns None if fewer than 2 runs have been recorded.
    """
    history = Path(snapshot_dir) / "history.jsonl"
    if not history.exists():
        return None

    lines = [ln for ln in history.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) < 2:
        return None

    mid = len(lines) // 2
    ref_path = Path(snapshot_dir) / "_ref_split.jsonl"
    cur_path = Path(snapshot_dir) / "_cur_split.jsonl"
    ref_path.write_text("\n".join(lines[:mid]) + "\n", encoding="utf-8")
    cur_path.write_text("\n".join(lines[mid:]) + "\n", encoding="utf-8")
    return str(ref_path), str(cur_path)


# ── Phase orchestration ───────────────────────────────────────────────────────

async def _collect_quality(tenant_id: str) -> QualityMetrics:
    """Pull quality scores from quality_evaluator."""
    try:
        from benchmarking.quality_evaluator import run_quality_evaluation
        summary = await run_quality_evaluation(tenant_id, sample_size=30)
        return QualityMetrics(
            faithfulness=summary.get("avg_faithfulness", 0.0),
            answer_relevancy=summary.get("avg_answer_relevancy", 0.0),
            hallucination_rate=summary.get("hallucination_rate", 0.0),
        )
    except Exception as exc:
        logger.warning(f"UNIFIED_LOGGER: quality_evaluator failed — {exc}")
        return QualityMetrics()


async def _collect_latency(tenant_id: str, n_iterations: int = 5) -> LatencyMetrics:
    """
    BUG 1 FIX: the old code called percentile_report(n_queries=10) which raises
    TypeError because percentile_report(times_ms: list[float]) takes a list, not
    a keyword argument. We now run stage_profiler() n_iterations times, collect
    total_pipeline_ms from each call, then pass the populated list to percentile_report().

    BUG 3 FIX: the old function was sync and used asyncio.get_event_loop()
    .run_until_complete() which raises RuntimeError in Python 3.10+ when called
    inside a running event loop. Function is now async; all calls use await.
    """
    try:
        from benchmarking.latency_profiler import stage_profiler, percentile_report

        probe_query = "What is the main contribution of this paper?"
        times_ms: list[float] = []
        for _ in range(n_iterations):
            stages = await stage_profiler(probe_query, tenant_id)
            total = stages.get("total_pipeline_ms")
            if total is not None:
                times_ms.append(float(total))

        if not times_ms:
            logger.warning("UNIFIED_LOGGER: All stage_profiler iterations failed — latency set to 0.0.")
            return LatencyMetrics()

        report = percentile_report(times_ms)
        return LatencyMetrics(
            p50_ms=report.get("p50_ms", 0.0),
            p95_ms=report.get("p95_ms", 0.0),
            p99_ms=report.get("p99_ms", 0.0),
            # ttft requires the SSE token stream; mean pipeline time is the best
            # proxy available here without a BENCHMARK_TOKEN
            ttft_ms=report.get("mean_ms", 0.0),
        )
    except Exception as exc:
        logger.warning(f"UNIFIED_LOGGER: latency_profiler failed — {exc}")
        return LatencyMetrics()


def _collect_cost() -> CostMetrics:
    """
    BUG 2 FIX: the old code accessed three wrong keys from per_query_breakdown():
      "avg_cost_per_query_usd"  → actual key is "avg_usd"
      "avg_tokens_per_query"    → actual key is "avg_tokens_in"
      "estimated_cache_savings_usd" → not in per_query_breakdown() at all;
                                      it lives in cache_savings_calculator()
    Fixed by using the correct key names and calling cache_savings_calculator()
    separately for the savings figure.
    """
    try:
        from benchmarking.cost_tracker import per_query_breakdown, cache_savings_calculator

        breakdown = per_query_breakdown(n=30)
        savings   = cache_savings_calculator()
        return CostMetrics(
            cost_per_query_usd=breakdown.get("avg_usd", 0.0),
            tokens_per_query=breakdown.get("avg_tokens_in", 0.0),
            cache_savings_usd=savings.get("savings_usd", 0.0),
        )
    except Exception as exc:
        logger.warning(f"UNIFIED_LOGGER: cost_tracker failed — {exc}")
        return CostMetrics()


async def _collect_safety(tenant_id: str) -> SafetyMetrics:
    """
    BUG 3 FIX: the old function was sync and called
    asyncio.get_event_loop().run_until_complete(run_safety_benchmark()) which
    raises RuntimeError: This event loop is already running in Python 3.10+.
    Function is now async and directly awaits run_safety_benchmark().
    """
    try:
        from benchmarking.safety_evaluator import run_safety_benchmark
        result = await run_safety_benchmark()
        return SafetyMetrics(
            injection_block_rate=result.injection_block_rate,
            pii_leak_rate=result.pii_leak_rate,
            refusal_rate=result.refusal_rate,
        )
    except Exception as exc:
        logger.warning(f"UNIFIED_LOGGER: safety_evaluator failed — {exc}")
        return SafetyMetrics()


def _reliability_from_db(tenant_id: str) -> ReliabilityMetrics:
    """
    Sync DB helper called via run_in_executor from _collect_reliability().

    Reads UsageLog rows from the last 7 days and computes:
      error_rate     — fraction of query rows where metrics_json.error is truthy
      timeout_rate   — fraction of query rows where metrics_json.timeout is truthy
      cache_hit_rate — fraction of query rows where metrics_json.cache_hit is truthy
      worker_failure_rate — 0.0 (arq does not expose per-job failure counts via
                            a stable client API; use worker_benchmarker in
                            integration tests to measure this separately)
    """
    try:
        from app.core.database import UsageLog, SessionLocal

        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        db = SessionLocal()
        try:
            rows = (
                db.query(UsageLog)
                .filter(
                    UsageLog.tenant_id == tenant_id,
                    UsageLog.event_type == "query",
                    UsageLog.timestamp >= cutoff,
                )
                .all()
            )

            if not rows:
                logger.info(
                    f"UNIFIED_LOGGER: No UsageLog query rows in the last 7 days for "
                    f"tenant '{tenant_id}' — reliability metrics defaulting to 0.0."
                )
                return ReliabilityMetrics()

            total = len(rows)
            errors = 0
            timeouts = 0
            cache_hits = 0

            for r in rows:
                if not r.metrics_json:
                    continue
                try:
                    m = json.loads(r.metrics_json)
                    if m.get("error"):
                        errors += 1
                    if m.get("timeout"):
                        timeouts += 1
                    if m.get("cache_hit"):
                        cache_hits += 1
                except (json.JSONDecodeError, TypeError):
                    pass

            return ReliabilityMetrics(
                error_rate=round(errors / total, 4),
                timeout_rate=round(timeouts / total, 4),
                cache_hit_rate=round(cache_hits / total, 4),
                worker_failure_rate=0.0,
            )
        finally:
            db.close()
    except Exception as exc:
        logger.warning(f"UNIFIED_LOGGER: reliability collection failed — {exc}")
        return ReliabilityMetrics()


async def _collect_reliability(tenant_id: str) -> ReliabilityMetrics:
    """
    BUG 4 FIX: ReliabilityMetrics was never collected — always 0.0 in every
    scorecard run. Offloads the blocking SQLAlchemy query to a thread via
    run_in_executor so the event loop is not stalled.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _reliability_from_db, tenant_id)


async def weekly_run(
    tenant_id: str,
    experiment_name: str = "researchhub-weekly",
    run_drift: bool = True,
) -> BenchmarkResult:
    """
    Full weekly benchmark orchestrator.

    1. Collects metrics from all Phase 1 + 2 modules concurrently.
    2. Builds a BenchmarkResult scorecard across all five dimensions.
    3. Logs to MLflow with git commit tag.
    4. Appends a JSON line to history.jsonl for cumulative drift tracking.
    5. Generates an Evidently drift report by splitting history.jsonl into halves.
    6. Prints the PASS/FAIL scorecard table.

    Returns the BenchmarkResult for programmatic use.
    """
    logger.info(f"UNIFIED_LOGGER: Starting weekly benchmark run for tenant '{tenant_id}'.")
    commit = _git_commit_hash()
    run_id = f"weekly-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{commit}"

    # Run all async collectors concurrently — each is independently fault-tolerant
    quality, latency, safety, reliability = await asyncio.gather(
        _collect_quality(tenant_id),
        _collect_latency(tenant_id),
        _collect_safety(tenant_id),
        _collect_reliability(tenant_id),
    )

    # Cost uses sync SQLAlchemy — offload to thread pool to avoid blocking the loop
    loop = asyncio.get_running_loop()
    cost = await loop.run_in_executor(None, _collect_cost)

    result = BenchmarkResult(
        run_id=run_id,
        quality=quality,
        latency=latency,
        cost=cost,
        safety=safety,
        reliability=reliability,
    )

    # Scorecard to stdout
    result.summary()

    # MLflow
    mlflow_run_id = log_to_mlflow(result, experiment_name=experiment_name)
    if mlflow_run_id:
        result.run_id = mlflow_run_id

    # Append snapshot and run drift if ≥2 runs exist
    save_snapshot(result)
    if run_drift:
        split_paths = _load_latest_snapshot()
        if split_paths:
            ref_path, cur_path = split_paths
            generate_drift_report(
                reference_path=ref_path,
                current_path=cur_path,
            )
        else:
            logger.info("UNIFIED_LOGGER: Fewer than 2 snapshots recorded — drift report skipped.")

    logger.info(f"UNIFIED_LOGGER: Weekly run complete. run_id={result.run_id}")
    return result


if __name__ == "__main__":
    tenant_id = os.getenv("BENCHMARK_TENANT_ID", "default")

    print("=" * 55)
    print("  ResearchHub Unified Logger — Weekly Run")
    print("=" * 55)
    print(f"\nTenant  : {tenant_id}")
    print(f"Commit  : {_git_commit_hash()}\n")

    result = asyncio.run(weekly_run(tenant_id))
    print(f"\nRun ID  : {result.run_id}")
    print(f"MLflow  : {os.getenv('MLFLOW_TRACKING_URI', 'http://localhost:5000')}")
