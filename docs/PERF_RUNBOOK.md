# Performance Runbook

## Goal

This project does not need heavy performance engineering yet, but it does need a repeatable way to answer:

- Where is time actually going?
- Did a change improve or regress response time?
- How expensive are ingest, retrieval, and streaming query paths?

The minimal toolkit for that lives in `perf/`.

## Files

- `perf/benchmark_api.py`
  - repeatable request benchmarking
  - measures API latency for health, query, and ingest paths
- `perf/profile_hotspots.py`
  - targeted local profiling with `cProfile`
  - currently supports `search_vdb`
- `docs/PERF_BASELINE.md`
  - place to store benchmark results over time

## When to benchmark

Use benchmarking when you want before/after numbers.

Good examples:

- query latency before and after cache changes
- ingest time before and after OCR/docling changes
- first-event latency before and after graph prompt/tool changes
- query time with and without reranking

## When to profile

Use profiling when you want to find a hotspot inside the code.

Good examples:

- why `search_vdb()` feels slow
- whether reranking dominates retrieval cost
- whether OpenAI calls or local work dominate the request

## Benchmark commands

### Health endpoint

```powershell
python perf/benchmark_api.py --mode health --runs 10
```

### Query endpoint

You need a valid bearer token from `/api/v1/auth/token`.

```powershell
python perf/benchmark_api.py --mode query --token "<TOKEN>" --query "Summarize the latest uploaded paper." --runs 5
```

This reports:

- end-to-end query time
- first-event latency for the SSE stream

### Ingest endpoint

```powershell
python perf/benchmark_api.py --mode ingest --token "<TOKEN>" --ingest-file test.pdf --runs 2
```

Run ingest benchmarks sparingly because they are expensive and mutate the corpus.

## Profiling commands

### Retrieval hotspot

```powershell
python perf/profile_hotspots.py --target search_vdb --tenant-id "<TENANT_ID>" --query "transformer architecture attention" --limit 5
```

Optional:

```powershell
python perf/profile_hotspots.py --target search_vdb --tenant-id "<TENANT_ID>" --output scratch/search_vdb_profile.txt
```

## Suggested baseline metrics

Keep at least these:

- health endpoint latency
- query cache hit latency
- query cache miss latency
- query first-event latency
- ingest total time
- retrieval time with reranking enabled

## Practical rules

- Benchmark with representative prompts and documents, not toy strings only.
- Compare cache-hit and cache-miss separately.
- Profile only a hotspot you are actively changing.
- Save results in `docs/PERF_BASELINE.md` so regressions are visible over time.
- Do not add perf gates to CI until workloads are stable and repeatable.
