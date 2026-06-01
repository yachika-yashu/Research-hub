"""
ResearchHub Benchmarking Suite — Phase 1.

Low-risk, purely additive modules that read existing infrastructure
(UsageLog, Qdrant, Redis) and never touch hot-path code.

Phase 1 modules (all LOW risk — read-only):
  scorecard         — BenchmarkResult dataclass + summary/to_dict
  cost_tracker      — per-query and per-ingest cost breakdown from UsageLog
  latency_profiler  — stage-level timing + wraps perf.benchmark_api
  data_quality      — chunk quality audit + retrieval precision@3
  worker_benchmarker — external worker timing via Redis pub/sub
  safety_evaluator  — guardrail injection probe + PII leak detection
"""
