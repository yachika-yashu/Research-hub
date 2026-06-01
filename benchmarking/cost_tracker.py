"""
Cost tracker — reads UsageLog from PostgreSQL and wraps estimate_cost().

Never rewrites estimate_cost() or count_tokens() — imports and calls them.
Runnable standalone: python -m benchmarking.cost_tracker
"""
import json
import os
from typing import Optional

from app.core.logic import estimate_cost
from app.core.database import UsageLog, SessionLocal
from app.core.config import GENERATION_MODEL, EMBEDDING_MODEL
from app.core.logging import logger


def per_query_breakdown(n: int = 50) -> dict:
    """
    Read the last N query-type UsageLog rows and return cost statistics.

    Returns avg, p95, max, and total cost across the sampled rows.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(UsageLog)
            .filter(UsageLog.event_type == "query")
            .order_by(UsageLog.timestamp.desc())
            .limit(n)
            .all()
        )
        if not rows:
            logger.info("COST_TRACKER: No query rows found in UsageLog.")
            return {"count": 0, "avg_usd": 0.0, "p95_usd": 0.0, "max_usd": 0.0, "total_usd": 0.0}

        costs = sorted(r.estimated_cost_usd or 0.0 for r in rows)
        p95_idx = max(0, int(len(costs) * 0.95) - 1)

        return {
            "count": len(costs),
            "avg_usd": round(sum(costs) / len(costs), 6),
            "p95_usd": round(costs[p95_idx], 6),
            "max_usd": round(max(costs), 6),
            "total_usd": round(sum(costs), 6),
            "avg_tokens_in": round(sum(r.tokens_input or 0 for r in rows) / len(rows), 1),
            "avg_tokens_out": round(sum(r.tokens_output or 0 for r in rows) / len(rows), 1),
        }
    finally:
        db.close()


def per_ingestion_breakdown() -> dict:
    """
    Read all ingest-type UsageLog rows and return cost-per-PDF statistics.

    Useful for estimating how much each uploaded document costs in embeddings.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(UsageLog)
            .filter(UsageLog.event_type == "ingest")
            .order_by(UsageLog.timestamp.desc())
            .all()
        )
        if not rows:
            logger.info("COST_TRACKER: No ingest rows found in UsageLog.")
            return {"count": 0, "avg_usd": 0.0, "max_usd": 0.0, "total_usd": 0.0}

        costs = [r.estimated_cost_usd or 0.0 for r in rows]
        token_totals = [r.tokens_input or 0 for r in rows]

        return {
            "count": len(costs),
            "avg_usd": round(sum(costs) / len(costs), 6),
            "max_usd": round(max(costs), 6),
            "total_usd": round(sum(costs), 6),
            "avg_tokens_per_pdf": round(sum(token_totals) / len(token_totals), 1),
        }
    finally:
        db.close()


def cache_savings_calculator() -> dict:
    """
    Estimate cost avoided by exact and semantic cache hits.

    Cache hits never write a UsageLog row, so we infer savings from metrics_json
    fields that record cache_hit=True on rows that do get logged. The per-call
    cost is estimated from the average token counts of logged queries.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(UsageLog)
            .filter(UsageLog.event_type == "query")
            .all()
        )
        if not rows:
            return {"logged_queries": 0, "cache_hits_detected": 0, "savings_usd": 0.0}

        cache_hits = 0
        for r in rows:
            if r.metrics_json:
                try:
                    m = json.loads(r.metrics_json)
                    if m.get("cache_hit"):
                        cache_hits += 1
                except Exception:
                    pass

        avg_tokens_in  = sum(r.tokens_input  or 0 for r in rows) / len(rows)
        avg_tokens_out = sum(r.tokens_output or 0 for r in rows) / len(rows)
        cost_per_call  = estimate_cost(int(avg_tokens_in), int(avg_tokens_out), GENERATION_MODEL)

        return {
            "logged_queries": len(rows),
            "cache_hits_detected": cache_hits,
            "avg_cost_per_call_usd": round(cost_per_call, 6),
            "savings_usd": round(cache_hits * cost_per_call, 6),
        }
    finally:
        db.close()


def monthly_projection(queries_per_day: float, ingestions_per_day: float) -> dict:
    """
    Project 30-day cost based on expected daily throughput.

    Uses average costs from actual UsageLog history as the unit cost per event.
    If no history exists, returns zeroes with a warning.
    """
    q = per_query_breakdown(n=200)
    i = per_ingestion_breakdown()

    avg_query_cost  = q.get("avg_usd", 0.0)
    avg_ingest_cost = i.get("avg_usd", 0.0)

    if avg_query_cost == 0.0 and avg_ingest_cost == 0.0:
        logger.warning("COST_TRACKER: No UsageLog history — projection based on zero averages.")

    query_30d  = queries_per_day    * 30 * avg_query_cost
    ingest_30d = ingestions_per_day * 30 * avg_ingest_cost

    return {
        "queries_per_day": queries_per_day,
        "ingestions_per_day": ingestions_per_day,
        "projected_query_cost_30d_usd": round(query_30d, 4),
        "projected_ingest_cost_30d_usd": round(ingest_30d, 4),
        "projected_total_30d_usd": round(query_30d + ingest_30d, 4),
        "based_on_avg_query_cost_usd": avg_query_cost,
        "based_on_avg_ingest_cost_usd": avg_ingest_cost,
        "history_query_rows": q.get("count", 0),
        "history_ingest_rows": i.get("count", 0),
    }


if __name__ == "__main__":
    import json as _json

    print("=" * 55)
    print("  ResearchHub Cost Tracker")
    print("=" * 55)

    print("\nPer-Query Breakdown (last 50 rows):")
    print(_json.dumps(per_query_breakdown(), indent=2))

    print("\nPer-Ingestion Breakdown (all rows):")
    print(_json.dumps(per_ingestion_breakdown(), indent=2))

    print("\nCache Savings Estimate:")
    print(_json.dumps(cache_savings_calculator(), indent=2))

    print("\nMonthly Projection (100 queries/day, 5 ingestions/day):")
    print(_json.dumps(monthly_projection(100.0, 5.0), indent=2))
