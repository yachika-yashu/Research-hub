"""
BenchmarkResult scorecard — aggregates all benchmark dimensions into one structure.

Reads thresholds.json from the same directory. Never imports hot-path code.
"""
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.config import GENERATION_MODEL, EMBEDDING_MODEL

_THRESHOLDS_PATH = Path(__file__).parent / "thresholds.json"


def _load_thresholds() -> dict:
    """Load pass/fail thresholds from thresholds.json."""
    with open(_THRESHOLDS_PATH) as fh:
        return json.load(fh)


@dataclass
class QualityMetrics:
    """How good is RAG answer quality """
    faithfulness: float = 0.0
    context_recall: float = 0.0
    answer_relevancy: float = 0.0
    context_precision: float = 0.0
    hallucination_rate: float = 0.0


@dataclass
class LatencyMetrics:
    """how fast, End-to-end and per-stage latency in milliseconds."""
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    ttft_ms: float = 0.0
    ingestion_p99_ms: float = 0.0


@dataclass
class CostMetrics:
    """how pricier, Token and USD cost breakdown per query/ingestion cycle."""
    cost_per_query_usd: float = 0.0
    tokens_per_query: float = 0.0
    embedding_cost_usd: float = 0.0
    cache_savings_usd: float = 0.0


@dataclass
class SafetyMetrics:
    """how safe, Guardrail and PII safety scores."""
    pii_leak_rate: float = 0.0
    refusal_rate: float = 0.0
    injection_block_rate: float = 0.0


@dataclass
class ReliabilityMetrics:
    """how stable, Error, timeout, and cache reliability."""
    error_rate: float = 0.0
    timeout_rate: float = 0.0
    cache_hit_rate: float = 0.0
    worker_failure_rate: float = 0.0


@dataclass
class BenchmarkResult:
    """
    Full benchmark scorecard across five dimensions.

    Populated by running Phase 1 benchmarking modules and passed to
    summary() for a human-readable table or to_dict() for MLflow logging.
    """
    quality: QualityMetrics = field(default_factory=QualityMetrics)
    latency: LatencyMetrics = field(default_factory=LatencyMetrics)
    cost: CostMetrics = field(default_factory=CostMetrics)
    safety: SafetyMetrics = field(default_factory=SafetyMetrics)
    reliability: ReliabilityMetrics = field(default_factory=ReliabilityMetrics)
    run_id: Optional[str] = None
    timestamp: Optional[str] = None
    generation_model: str = field(default_factory=lambda: GENERATION_MODEL)
    embedding_model: str = field(default_factory=lambda: EMBEDDING_MODEL)

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def summary(self) -> None:
        """Print a formatted pass/fail table against thresholds.json."""
        thresholds = _load_thresholds()

        # (display_name, actual_value, threshold_key, higher_is_better)
        checks = [
            ("Faithfulness",          self.quality.faithfulness,        "faithfulness",          True),
            ("Context Recall",        self.quality.context_recall,      "context_recall",        True),
            ("P99 Latency (ms)",      self.latency.p99_ms,              "p99_latency_ms",        False),
            ("Cost/Query (USD)",      self.cost.cost_per_query_usd,     "cost_per_query_usd",    False),
            ("Worker P99 (ms)",       self.latency.ingestion_p99_ms,    "worker_p99_ms",         False),
            ("PII Leak Rate",         self.safety.pii_leak_rate,        "pii_leak_rate",         False),
            ("Injection Block Rate",  self.safety.injection_block_rate, "injection_block_rate",  True),
        ]

        print(f"\n{'=' * 70}")
        print(f"  ResearchHub Benchmark Scorecard")
        if self.run_id:
            print(f"  Run:   {self.run_id}")
        print(f"  Time:  {self.timestamp}")
        print(f"  Model: {self.generation_model} / {self.embedding_model}")
        print(f"{'=' * 70}")
        print(f"  {'Metric':<30} {'Value':>10} {'Threshold':>12} {'':>8}")
        print(f"  {'-' * 64}")

        all_pass = True
        for name, value, key, higher_is_better in checks:
            threshold = thresholds.get(key)
            if threshold is None:
                status = "    N/A"
                threshold_str = "       —"
            elif higher_is_better:
                passed = value >= threshold
                status = "   PASS" if passed else "   FAIL"
                threshold_str = f"{threshold:>12.4f}"
                if not passed:
                    all_pass = False
            else:
                passed = value <= threshold
                status = "   PASS" if passed else "   FAIL"
                threshold_str = f"{threshold:>12.4f}"
                if not passed:
                    all_pass = False

            print(f"  {name:<30} {value:>10.4f} {threshold_str} {status}")

        print(f"{'=' * 70}")
        overall = "ALL PASS" if all_pass else "SOME FAILURES — see above"
        print(f"  Overall: {overall}")
        print(f"{'=' * 70}\n")

    def to_dict(self) -> dict:
        """
        Flat dict of all metrics, suitable for MLflow log_metrics() or JSON dump.

        Keys use dot notation: quality.faithfulness, latency.p99_ms, etc.
        """
        result: dict = {}
        sections = [
            ("quality",     self.quality),
            ("latency",     self.latency),
            ("cost",        self.cost),
            ("safety",      self.safety),
            ("reliability", self.reliability),
        ]
        for section_name, section_obj in sections:
            for k, v in asdict(section_obj).items():
                result[f"{section_name}.{k}"] = v

        result["run_id"]           = self.run_id or ""
        result["timestamp"]        = self.timestamp or ""
        result["generation_model"] = self.generation_model
        result["embedding_model"]  = self.embedding_model
        return result


if __name__ == "__main__":
    # Demo: create a sample result and print the scorecard.
    demo = BenchmarkResult(run_id="demo-run-001")
    demo.quality.faithfulness        = 0.87
    demo.quality.context_recall      = 0.81
    demo.latency.p99_ms              = 4200.0
    demo.latency.ingestion_p99_ms    = 22000.0
    demo.cost.cost_per_query_usd     = 0.0055
    demo.safety.pii_leak_rate        = 0.0
    demo.safety.injection_block_rate = 0.97
    demo.summary()
    print("to_dict() output (first 5 keys):")
    d = demo.to_dict()
    for k in list(d)[:5]:
        print(f"  {k}: {d[k]}")
