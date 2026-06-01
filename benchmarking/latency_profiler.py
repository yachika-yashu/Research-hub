"""
Latency profiler — extends perf.benchmark_api, never rewrites its functions.

Adds stage-level breakdown, TTFT measurement, and numpy-based percentile report.
Runnable standalone: python -m benchmarking.latency_profiler
"""
import asyncio
import os
import time
from typing import Optional

import numpy as np

from perf.benchmark_api import benchmark_query, benchmark_ingest, summarize_run
from app.core.cache import exact_cache_get
from app.services.vector_store import search_vdb
from app.core.globals import openai_client
from app.core.config import GENERATION_MODEL, GENERATION_TEMP
from app.core.logging import logger


def percentile_report(times_ms: list[float]) -> dict:
    """
    Compute P50 / P95 / P99 statistics from a list of millisecond timings.

    Uses numpy for accurate interpolated percentiles. Returns zeros if the
    list is empty.
    """
    if not times_ms:
        return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0,
                "min_ms": 0.0, "max_ms": 0.0, "mean_ms": 0.0}

    arr = np.array(times_ms, dtype=float)
    return {
        "count": len(times_ms),
        "p50_ms":  round(float(np.percentile(arr, 50)),  2),
        "p95_ms":  round(float(np.percentile(arr, 95)),  2),
        "p99_ms":  round(float(np.percentile(arr, 99)),  2),
        "min_ms":  round(float(arr.min()),  2),
        "max_ms":  round(float(arr.max()),  2),
        "mean_ms": round(float(arr.mean()), 2),
    }


async def stage_profiler(query: str, tenant_id: str) -> dict:
    """
    Time each RAG pipeline stage independently by calling real infrastructure.

    Stages measured:
    1. Redis exact-cache lookup (exact_cache_get)
    2. Qdrant hybrid search (search_vdb)
    3. OpenAI LLM round-trip (short prompt, max_tokens=50)

    Each stage is timed with perf_counter. Failures are caught and logged;
    the failed stage's value is set to None so callers can detect partial runs.
    """
    timings: dict[str, Optional[float]] = {}

    # Stage 1: Redis exact-cache check
    t0 = time.perf_counter()
    try:
        await exact_cache_get(tenant_id, query)
        timings["cache_check_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    except Exception as exc:
        logger.warning(f"PROFILER: Cache stage failed — {exc}")
        timings["cache_check_ms"] = None

    # Stage 2: Qdrant hybrid vector search
    t1 = time.perf_counter()
    try:
        await search_vdb(query, tenant_id, limit=5)
        timings["vector_search_ms"] = round((time.perf_counter() - t1) * 1000, 2)
    except Exception as exc:
        logger.warning(f"PROFILER: VDB stage failed — {exc}")
        timings["vector_search_ms"] = None

    # Stage 3: LLM generation round-trip (minimal prompt to isolate API RTT)
    t2 = time.perf_counter()
    try:
        await openai_client.chat.completions.create(
            model=GENERATION_MODEL,
            messages=[{"role": "user", "content": f"One sentence: {query}"}],
            temperature=GENERATION_TEMP,
            max_tokens=50,
        )
        timings["llm_generation_ms"] = round((time.perf_counter() - t2) * 1000, 2)
    except Exception as exc:
        logger.warning(f"PROFILER: LLM stage failed — {exc}")
        timings["llm_generation_ms"] = None

    valid_values = [v for v in timings.values() if v is not None]
    timings["total_pipeline_ms"] = round(sum(valid_values), 2)
    return timings


def ttft_profiler(query: str, base_url: str, token: str) -> float:
    """
    Measure Time-To-First-Token (TTFT) from the /query SSE endpoint.

    Delegates to benchmark_query with runs=1 and returns the first-event
    p50 latency. This is the wall-clock time from request send to the moment
    the first SSE data line arrives.
    """
    result = benchmark_query(base_url, token, query, runs=1)
    return result["first_event"].get("p50_ms", 0.0)


if __name__ == "__main__":
    import argparse
    import json as _json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="ResearchHub Latency Profiler")
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Write JSON percentile report to this file (default: stdout)",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=int(os.getenv("BENCHMARK_N", "3")),
        help="Number of stage_profiler iterations for percentile computation (default: 3)",
    )
    parser.add_argument(
        "--tenant",
        default=os.getenv("BENCHMARK_TENANT_ID", "default"),
        help="Tenant ID used for search_vdb and cache probes",
    )
    parser.add_argument(
        "--url",
        default=os.getenv("BENCHMARK_BASE_URL", "http://localhost:8000/api/v1"),
        help="API base URL (only used when BENCHMARK_TOKEN is set for TTFT profiling)",
    )
    args = parser.parse_args()

    query = os.getenv("BENCHMARK_QUERY", "What datasets were used in the uploaded papers?")
    token = os.getenv("BENCHMARK_TOKEN", "")

    async def _run_n_stages(n: int, q: str, t: str) -> list[float]:
        """Run stage_profiler n times, collect total_pipeline_ms values."""
        totals = []
        for _ in range(n):
            s = await stage_profiler(q, t)
            v = s.get("total_pipeline_ms")
            if v is not None:
                totals.append(float(v))
        return totals

    if args.output:
        # CI mode: run N iterations, compute percentiles, write JSON, no headers
        timings = asyncio.run(_run_n_stages(args.n, query, args.tenant))
        report = percentile_report(timings)
        # Also capture a single detailed breakdown for diagnostics
        stages = asyncio.run(stage_profiler(query, args.tenant))
        report["stages_sample"] = stages
        out_json = _json.dumps(report, indent=2)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(out_json)
    else:
        # Human mode: print full breakdown with headers
        print("=" * 55)
        print("  ResearchHub Latency Profiler")
        print("=" * 55)
        print(f"\nQuery: {query}")
        print(f"Tenant: {args.tenant}  |  Iterations: {args.n}\n")

        print("Stage-Level Breakdown:")
        stages = asyncio.run(stage_profiler(query, args.tenant))
        print(_json.dumps(stages, indent=2))

        timings = asyncio.run(_run_n_stages(args.n, query, args.tenant))
        if timings:
            print(f"\nPercentile Report ({args.n} iterations):")
            print(_json.dumps(percentile_report(timings), indent=2))

        if token:
            print("\nTime-To-First-Token (TTFT):")
            ttft = ttft_profiler(query, args.url, token)
            print(f"  {ttft:.1f} ms")

            print("\nFull Query Benchmark (3 runs):")
            qr = benchmark_query(args.url, token, query, runs=3)
            print(_json.dumps(qr, indent=2))
        else:
            print("\n(Set BENCHMARK_TOKEN env var to run TTFT and full query benchmark)")
