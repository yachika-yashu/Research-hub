import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

import httpx


def build_headers(token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[index]


def summarize_run(name: str, durations_ms: list[float]) -> dict[str, Any]:
    return {
        "name": name,
        "count": len(durations_ms),
        "min_ms": round(min(durations_ms), 2),
        "avg_ms": round(statistics.mean(durations_ms), 2),
        "p50_ms": round(percentile(durations_ms, 50), 2),
        "p95_ms": round(percentile(durations_ms, 95), 2),
        "max_ms": round(max(durations_ms), 2),
    }


def benchmark_health(base_url: str, runs: int) -> dict[str, Any]:
    durations_ms: list[float] = []
    with httpx.Client(timeout=30.0) as client:
        for _ in range(runs):
            started = time.perf_counter()
            response = client.get(f"{base_url}/health")
            response.raise_for_status()
            durations_ms.append((time.perf_counter() - started) * 1000)
    return summarize_run("health", durations_ms)


def benchmark_query(base_url: str, token: str, query: str, runs: int) -> dict[str, Any]:
    """
    Measure end-to-end SSE query time plus first-event latency.

    We benchmark the actual streaming endpoint because that is the user-facing
    path that exercises cache lookup, graph orchestration, and token delivery.
    """
    payload = {"query": query, "filters": {"year_min": 1990, "year_max": 2026}}
    headers = build_headers(token)
    total_durations_ms: list[float] = []
    first_event_latencies_ms: list[float] = []

    with httpx.Client(timeout=120.0) as client:
        for _ in range(runs):
            started = time.perf_counter()
            first_event_ms = None
            with client.stream("POST", f"{base_url}/query", json=payload, headers=headers) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    if first_event_ms is None:
                        first_event_ms = (time.perf_counter() - started) * 1000
                    if line == "data: [DONE]":
                        break
            total_durations_ms.append((time.perf_counter() - started) * 1000)
            first_event_latencies_ms.append(first_event_ms or 0.0)

    return {
        "query": query,
        "total": summarize_run("query_total", total_durations_ms),
        "first_event": summarize_run("query_first_event", first_event_latencies_ms),
    }


def benchmark_ingest(base_url: str, token: str, file_path: Path, runs: int) -> dict[str, Any]:
    """
    Benchmark the full ingestion SSE lifecycle for one representative document.

    This is intentionally a low-frequency benchmark because OCR, embedding, and
    Qdrant writes can be expensive.
    """
    headers = build_headers(token)
    durations_ms: list[float] = []

    with httpx.Client(timeout=600.0) as client:
        for _ in range(runs):
            started = time.perf_counter()
            with file_path.open("rb") as fh:
                files = {"file": (file_path.name, fh, "application/pdf")}
                with client.stream("POST", f"{base_url}/ingest", files=files, headers=headers) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        payload = line.removeprefix("data: ").strip()
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "completed":
                            break
            durations_ms.append((time.perf_counter() - started) * 1000)

    return summarize_run("ingest_total", durations_ms)


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal API benchmark harness for Hanuman.")
    parser.add_argument("--base-url", default="http://localhost:8000/api/v1", help="FastAPI base URL")
    parser.add_argument("--token", help="Bearer token for authenticated endpoints")
    parser.add_argument("--query", default="Summarize the latest uploaded paper.", help="Query text for /query")
    parser.add_argument("--runs", type=int, default=3, help="Number of benchmark iterations")
    parser.add_argument(
        "--mode",
        choices=["health", "query", "ingest", "all"],
        default="all",
        help="Which benchmark to run",
    )
    parser.add_argument("--ingest-file", type=Path, help="PDF path used for ingest benchmarking")
    args = parser.parse_args()

    results: dict[str, Any] = {}

    if args.mode in {"health", "all"}:
        results["health"] = benchmark_health(args.base_url, args.runs)

    if args.mode in {"query", "all"}:
        if not args.token:
            raise SystemExit("--token is required for query benchmarking.")
        results["query"] = benchmark_query(args.base_url, args.token, args.query, args.runs)

    if args.mode in {"ingest", "all"}:
        if not args.token:
            raise SystemExit("--token is required for ingest benchmarking.")
        if not args.ingest_file or not args.ingest_file.exists():
            raise SystemExit("--ingest-file must point to an existing PDF for ingest benchmarking.")
        results["ingest"] = benchmark_ingest(args.base_url, args.token, args.ingest_file, args.runs)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
