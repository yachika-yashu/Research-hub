"""
Worker benchmarker — external instrumentation only, never modifies app/worker.py.

Submits ingest jobs via the /ingest API, subscribes to Redis pub/sub on
`ingest:{job_id}` (the channel the worker publishes to), and records
wall-clock timestamps at each progress event.

Also provides benchmark_arq_job — a decorator that any arq job function can
use to auto-record processing time and success/failure into Redis.

Runnable standalone: python -m benchmarking.worker_benchmarker
"""
import asyncio
import functools
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import redis.asyncio as aioredis

from app.core.logging import logger

_BASE_URL  = os.getenv("BENCHMARK_BASE_URL", "http://localhost:8000/api/v1")
_REDIS_URL = os.getenv("REDIS_URL",          "redis://localhost:6379/0")

# Redis key template where per-job bench records are stored as a capped list.
# Each element is a JSON string with job_id, elapsed_ms, success, error, timestamp.
_BENCH_KEY = "arq:bench:{fn_name}"
_BENCH_CAP = 200  # keep the last N records per function


# ── arq job decorator ─────────────────────────────────────────────────────────

def benchmark_arq_job(fn):
    """
    Decorator for arq job functions.  Wraps any async arq job to measure:
      - processing_ms: wall-clock time from job start to completion
      - success:       True if the function returned normally
      - error:         exception message on failure (None on success)

    Results are written to the Redis list  arq:bench:{function_name}
    (capped at _BENCH_CAP entries) using the ctx["redis"] client that arq
    already creates in the worker startup function.  If Redis is unavailable,
    the record is dropped silently — the original job still runs normally.

    Re-raises any exception from the wrapped job so arq's own retry /
    failure handling is unaffected.

    Usage
    -----
    In app/worker.py:

        from benchmarking.worker_benchmarker import benchmark_arq_job

        @benchmark_arq_job
        async def process_ingestion_task(ctx, file_content, filename, ...):
            ...

    Reading results:

        from benchmarking.worker_benchmarker import get_recent_job_stats
        stats = await get_recent_job_stats("process_ingestion_task")
    """
    @functools.wraps(fn)
    async def wrapper(ctx, *args, **kwargs):
        start = time.perf_counter()
        success = False
        error_msg: Optional[str] = None

        try:
            result = await fn(ctx, *args, **kwargs)
            success = True
            return result
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            record = json.dumps({
                "job_id":     ctx.get("job_id", "unknown"),
                "function":   fn.__name__,
                "elapsed_ms": elapsed_ms,
                "success":    success,
                "error":      error_msg,
                "timestamp":  datetime.now(timezone.utc).isoformat(),
            })
            bench_key = _BENCH_KEY.format(fn_name=fn.__name__)
            try:
                redis_client = ctx.get("redis")
                if redis_client is not None:
                    await redis_client.lpush(bench_key, record)
                    await redis_client.ltrim(bench_key, 0, _BENCH_CAP - 1)
                    logger.info(
                        f"WORKER_BENCH: {fn.__name__} elapsed={elapsed_ms:.0f}ms  success={success}"
                    )
                else:
                    logger.debug("WORKER_BENCH: ctx['redis'] not available — bench record dropped.")
            except Exception as log_exc:
                logger.warning(f"WORKER_BENCH: Failed to write bench record — {log_exc}")

    return wrapper


async def get_recent_job_stats(
    function_name: str,
    redis_url: str = _REDIS_URL,
    n: int = 100,
) -> dict:
    """
    Read the last n bench records for a given arq job function and compute:
      - total_recorded:     number of records in Redis for this function
      - success_rate:       fraction of successful jobs
      - avg_processing_ms:  mean elapsed_ms across all records
      - p95_processing_ms:  95th-percentile elapsed_ms
      - p99_processing_ms:  99th-percentile elapsed_ms
      - recent_errors:      list of the 5 most recent error messages

    Returns a dict with zeroed stats if no records are found.
    """
    bench_key = _BENCH_KEY.format(fn_name=function_name)
    redis_client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        raw = await redis_client.lrange(bench_key, 0, n - 1)
    finally:
        await redis_client.aclose()

    if not raw:
        return {
            "function": function_name,
            "total_recorded": 0,
            "success_rate": 0.0,
            "avg_processing_ms": 0.0,
            "p95_processing_ms": 0.0,
            "p99_processing_ms": 0.0,
            "recent_errors": [],
        }

    records: list[dict] = []
    for r in raw:
        try:
            records.append(json.loads(r))
        except (json.JSONDecodeError, TypeError):
            pass

    times = sorted(r["elapsed_ms"] for r in records if "elapsed_ms" in r)
    successes = sum(1 for r in records if r.get("success"))
    errors = [r["error"] for r in records if r.get("error")][:5]

    def _pct(arr: list[float], p: float) -> float:
        if not arr:
            return 0.0
        idx = max(0, int(len(arr) * p / 100) - 1)
        return round(arr[idx], 2)

    return {
        "function": function_name,
        "total_recorded": len(records),
        "success_rate": round(successes / len(records), 4),
        "avg_processing_ms": round(sum(times) / len(times), 2) if times else 0.0,
        "p95_processing_ms": _pct(times, 95),
        "p99_processing_ms": _pct(times, 99),
        "recent_errors": errors,
    }


# ── Standalone benchmarking functions ─────────────────────────────────────────

async def time_ingestion_job(
    file_path: str,
    token: str,
    base_url: str = _BASE_URL,
    redis_url: str = _REDIS_URL,
) -> dict:
    """
    Submit one PDF via POST /ingest, subscribe to the worker's Redis pub/sub
    channel `ingest:{job_id}`, and record elapsed milliseconds at each
    progress event (0 → 10 → 25 → … → 100 pct) and on completion.

    Returns a dict of stage keys → elapsed_ms from job submission.
    The ingest API returns {"job_id": "...", "status": "enqueued"}.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Test PDF not found: {file_path}")

    # 1. Submit job via API, capture job_id
    async with httpx.AsyncClient(timeout=30.0) as client:
        with path.open("rb") as fh:
            resp = await client.post(
                f"{base_url}/ingest",
                files={"file": (path.name, fh, "application/pdf")},
                headers={"Authorization": f"Bearer {token}"},
            )
        resp.raise_for_status()
        payload = resp.json()

    job_id = payload["job_id"]
    logger.info(f"WORKER_BENCH: Job {job_id} enqueued for {path.name}")
    submit_time = time.perf_counter()
    stage_times: dict[str, float] = {}

    # 2. Subscribe to Redis channel and record timestamps per progress event
    redis = aioredis.from_url(redis_url, decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"ingest:{job_id}")

    try:
        # Timeout guard: don't wait more than 10 minutes per job
        deadline = time.perf_counter() + 600.0
        async for message in pubsub.listen():
            if time.perf_counter() > deadline:
                logger.warning("WORKER_BENCH: Job timed out after 600s.")
                break
            if message["type"] != "message":
                continue
            try:
                event = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue

            elapsed_ms = round((time.perf_counter() - submit_time) * 1000, 2)
            event_type = event.get("type", "")

            if event_type == "progress":
                value = event.get("value", 0)
                key = f"progress_{value}pct"
                if key not in stage_times:  # record first occurrence only
                    stage_times[key] = elapsed_ms
                    logger.info(f"WORKER_BENCH: {key} at {elapsed_ms:.0f} ms")

            if event_type in {"completed", "eof", "error"}:
                stage_times["total_ms"] = elapsed_ms
                if event_type == "error":
                    stage_times["error"] = event.get("message", "unknown")
                break
    finally:
        await pubsub.unsubscribe(f"ingest:{job_id}")
        await redis.aclose()

    return stage_times


async def ingestion_throughput_test(
    file_path: str,
    token: str,
    n_files: int = 3,
    base_url: str = _BASE_URL,
    redis_url: str = _REDIS_URL,
) -> dict:
    """
    Submit n_files ingest jobs concurrently and measure wall-clock throughput.

    All jobs run in parallel via asyncio.gather. Returns:
    - throughput_files_per_min: files that completed successfully per minute
    - p99_completion_ms: 99th-percentile per-file completion time
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Test PDF not found: {file_path}")

    wall_start = time.perf_counter()
    tasks = [
        time_ingestion_job(file_path, token, base_url=base_url, redis_url=redis_url)
        for _ in range(n_files)
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    wall_ms = (time.perf_counter() - wall_start) * 1000

    completion_times = [
        r["total_ms"] for r in raw_results
        if isinstance(r, dict) and "total_ms" in r and "error" not in r
    ]
    errors = sum(1 for r in raw_results if isinstance(r, Exception) or (isinstance(r, dict) and "error" in r))

    if not completion_times:
        return {"n_files": n_files, "successful": 0, "errors": errors,
                "throughput_files_per_min": 0.0, "p99_completion_ms": 0.0}

    sorted_times = sorted(completion_times)
    p99_idx = max(0, int(len(sorted_times) * 0.99) - 1)
    throughput = (len(completion_times) / (wall_ms / 1000)) * 60

    return {
        "n_files": n_files,
        "successful": len(completion_times),
        "errors": errors,
        "total_wall_ms": round(wall_ms, 2),
        "avg_completion_ms": round(sum(completion_times) / len(completion_times), 2),
        "p99_completion_ms": round(sorted_times[p99_idx], 2),
        "throughput_files_per_min": round(throughput, 2),
    }


async def queue_depth_monitor(
    duration_seconds: int = 30,
    redis_url: str = _REDIS_URL,
) -> list[dict]:
    """
    Poll the arq job queue depth in Redis every second for duration_seconds.

    arq stores pending jobs in the sorted set `arq:queue`. The depth is the
    number of elements in that set waiting to be picked up by a worker.

    FIX: uses redis_url parameter via aioredis.from_url() instead of the app
    singleton get_redis_client(), so test isolation works correctly.

    Returns a time-series list of {elapsed_s, queue_depth} dicts.
    """
    redis_client = aioredis.from_url(redis_url, decode_responses=True)
    series: list[dict] = []
    start = time.perf_counter()

    try:
        for _ in range(duration_seconds):
            elapsed = round(time.perf_counter() - start, 2)
            try:
                depth = await redis_client.zcard("arq:queue")
            except Exception as exc:
                logger.warning(f"WORKER_BENCH: Queue depth poll failed — {exc}")
                depth = -1
            series.append({"elapsed_s": elapsed, "queue_depth": depth})
            await asyncio.sleep(1.0)
    finally:
        await redis_client.aclose()

    return series


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(description="ResearchHub Worker Benchmarker")
    parser.add_argument(
        "--redis-url",
        default=os.getenv("REDIS_URL", _REDIS_URL),
        help="Redis URL to poll for arq:queue depth (default: REDIS_URL env var)",
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Write JSON results to this file instead of stdout (used by CI gate)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=15,
        help="Seconds to poll the queue depth monitor (default: 15)",
    )
    parser.add_argument(
        "--stats",
        metavar="FUNCTION_NAME",
        help="Read recent bench stats for a decorated arq job function from Redis",
    )
    args = parser.parse_args()

    token    = os.getenv("BENCHMARK_TOKEN",    "")
    pdf_path = os.getenv("BENCHMARK_PDF_PATH", "")

    results: dict = {}

    # Optional: read decorator-logged stats for a specific job function
    if args.stats:
        print(f"\nJob Bench Stats — {args.stats}:")
        stats = asyncio.run(get_recent_job_stats(args.stats, redis_url=args.redis_url))
        print(_json.dumps(stats, indent=2))
        results["job_stats"] = stats

    # Optional: run a full ingestion timing test if creds are available
    if token and pdf_path:
        print(f"\nSingle Ingestion Timing: {pdf_path}")
        timing = asyncio.run(time_ingestion_job(pdf_path, token, redis_url=args.redis_url))
        results["ingestion_timing"] = timing
        print(_json.dumps(timing, indent=2))
    else:
        print("\n(Set BENCHMARK_TOKEN and BENCHMARK_PDF_PATH to run ingestion tests)")

    # Always: poll queue depth
    print(f"\nQueue Depth Monitor ({args.duration}s):")
    series = asyncio.run(queue_depth_monitor(args.duration, redis_url=args.redis_url))
    results["queue_depth_series"] = series
    print(_json.dumps(series, indent=2))

    # Derive p99_completion_ms for the CI gate — 0.0 when no ingestion ran
    p99_ms = 0.0
    if "ingestion_timing" in results:
        p99_ms = float(results["ingestion_timing"].get("total_ms", 0.0))
    elif "job_stats" in results:
        p99_ms = float(results["job_stats"].get("p99_processing_ms", 0.0))
    results["p99_completion_ms"] = p99_ms

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(results, indent=2))
        print(f"\nResults written to {args.output}")
    else:
        print("=" * 55)
        print("  ResearchHub Worker Benchmarker — Summary")
        print("=" * 55)
        print(f"  p99_completion_ms : {p99_ms}")
        queue_depths = [s["queue_depth"] for s in series if s["queue_depth"] >= 0]
        if queue_depths:
            print(f"  max_queue_depth   : {max(queue_depths)}")
            print(f"  avg_queue_depth   : {round(sum(queue_depths) / len(queue_depths), 2)}")
