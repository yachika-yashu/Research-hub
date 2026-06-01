#!/usr/bin/env python3
"""
ResearchHub Smoke Test — HTTP mode.

Exercises the live API over HTTP so it works from any machine regardless of
whether app Python dependencies (qdrant-client, fastembed, etc.) are locally
installed.  No Docker exec required.

Usage:
    python benchmarking/smoke_test.py [--url URL] [--tenant TENANT_ID] [--token TOKEN]

Exit codes:
    0 — all questions passed (non-empty, coherent answers)
    1 — one or more questions failed or returned empty responses
    2 — pre-flight check failed (API unreachable or vault empty)
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows cmd/PowerShell defaults to cp1252 which cannot encode Unicode box
# drawing characters.  Force UTF-8 so the progress output renders correctly.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import httpx
    from httpx_sse import aconnect_sse
except ImportError:
    print("ERROR: httpx and httpx-sse are required.  Run:  pip install httpx httpx-sse")
    sys.exit(2)

# Three generic structural questions that any non-empty research vault
# should be able to answer with a coherent non-empty response.
_SMOKE_QUESTIONS = [
    "What is this paper about?",
    "What methodology was used in this research?",
    "What are the main findings or results?",
]

_MIN_ANSWER_CHARS = 50    # answers shorter than this are treated as failures
_TIMEOUT_SECONDS  = 60    # hard timeout per question


# ── Pre-flight ─────────────────────────────────────────────────────────────────

async def _health_check(base_url: str) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{base_url}/health")
            if r.status_code == 200:
                return True, r.json().get("version", "?")
            return False, f"HTTP {r.status_code}"
    except Exception as exc:
        return False, str(exc)


async def _get_or_create_token(base_url: str, provided_token: str) -> tuple[str, str]:
    """
    Return (token, tenant_id).
    Uses provided_token if set.  Otherwise registers a temporary test user.
    """
    if provided_token:
        # Derive tenant by inspecting /vault/papers (any authed call works)
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"{base_url}/vault/papers",
                headers={"Authorization": f"Bearer {provided_token}"},
            )
            if r.status_code == 200:
                return provided_token, ""
        return provided_token, ""

    # Register + login a throwaway test account
    test_user     = "smoketest_auto_user"
    test_password = "SmokeTest!1234"
    test_team     = "smoketest_team"

    async with httpx.AsyncClient(timeout=10.0) as c:
        # Register (ignore 400 if already exists)
        await c.post(
            f"{base_url}/auth/register",
            json={"username": test_user, "password": test_password,
                  "team_code": test_team},
        )
        # Login
        r = await c.post(
            f"{base_url}/auth/token",
            data={"username": test_user, "password": test_password},
        )
        if r.status_code != 200:
            raise RuntimeError(f"Login failed: {r.text}")
        data = r.json()
        return data["access_token"], data.get("tenant_id", "")


async def preflight(base_url: str, token: str) -> bool:
    """Check API health and that the vault has at least one paper."""
    print("Pre-flight checks...")

    ok, version = await _health_check(base_url)
    if ok:
        print(f"  OK  API health — version {version}")
    else:
        print(f"  FAIL  API unreachable — {version}")
        print()
        print("FATAL: API is not running.")
        print("Start the stack:  docker compose up -d")
        return False

    # Check vault occupancy
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(
                f"{base_url}/vault/papers",
                headers={"Authorization": f"Bearer {token}"},
            )
            papers = r.json().get("papers", [])
            if papers:
                print(f"  OK  Vault — {len(papers)} paper(s) found")
            else:
                print("  WARN  Vault is empty — ingest a paper before running smoke test")
                print()
                print("WARNING: smoke test will run but all answers will fail (nothing to retrieve).")
                print("Ingest via:  POST /api/v1/ingest  or dashboard upload")
    except Exception as exc:
        print(f"  FAIL  Vault check — {exc}")
        return False

    print()
    return True


# ── Question runner ────────────────────────────────────────────────────────────

async def _ask_one(base_url: str, token: str, question: str) -> dict:
    """
    Send one question to the /query SSE endpoint and collect the full answer.
    Returns {question, answer, passed, elapsed_s, error}.
    """
    t0 = datetime.now(timezone.utc)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "query":   question,
        "filters": {"year_min": 1990, "year_max": 2026},
    }
    sse_timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
    full_answer = ""
    cache_hit   = False
    error_msg   = None

    try:
        async with httpx.AsyncClient(timeout=sse_timeout) as client:
            async with aconnect_sse(
                client, "POST", f"{base_url}/query",
                json=payload, headers=headers
            ) as es:
                async for ev in es.aiter_sse():
                    if ev.data == "[DONE]":
                        break
                    try:
                        evt = json.loads(ev.data)
                    except (json.JSONDecodeError, ValueError):
                        continue

                    if evt.get("status") == "cache_hit":
                        full_answer = evt.get("content", "")
                        cache_hit   = True
                    elif evt.get("type") == "token":
                        full_answer += evt.get("content", "")
                    elif evt.get("type") == "error":
                        error_msg = evt.get("content", "unknown error")

    except asyncio.TimeoutError:
        error_msg = f"timed out after {_TIMEOUT_SECONDS}s"
    except Exception as exc:
        error_msg = str(exc)

    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    passed  = (
        not error_msg
        and len(full_answer.strip()) >= _MIN_ANSWER_CHARS
    )
    return {
        "question": question,
        "answer":   full_answer[:200],
        "passed":   passed,
        "cache_hit": cache_hit,
        "elapsed_s": round(elapsed, 2),
        "error":    error_msg,
        "reason":   (
            error_msg
            or (f"answer too short ({len(full_answer.strip())} chars < {_MIN_ANSWER_CHARS})"
                if not passed else "")
        ),
    }


# ── Main runner ────────────────────────────────────────────────────────────────

async def run_smoke_test(base_url: str, token: str) -> int:
    total_start = datetime.now(timezone.utc)
    print(f"Running {len(_SMOKE_QUESTIONS)} smoke question(s) "
          f"(min answer length: {_MIN_ANSWER_CHARS} chars)\n")

    results: list[dict] = []

    for idx, question in enumerate(_SMOKE_QUESTIONS, 1):
        print(f"  [{idx}/{len(_SMOKE_QUESTIONS)}] {question}")
        try:
            result = await asyncio.wait_for(
                _ask_one(base_url, token, question),
                timeout=_TIMEOUT_SECONDS + 5,
            )
        except asyncio.TimeoutError:
            result = {
                "question":  question,
                "answer":    "",
                "passed":    False,
                "cache_hit": False,
                "elapsed_s": float(_TIMEOUT_SECONDS),
                "error":     f"hard timeout after {_TIMEOUT_SECONDS + 5}s",
                "reason":    f"hard timeout after {_TIMEOUT_SECONDS + 5}s",
            }

        icon   = "OK " if result["passed"] else "FAIL"
        cache  = " [cache]" if result["cache_hit"] else ""
        print(f"         {icon}  {result['elapsed_s']:.1f}s{cache}  "
              f"answer={len(result['answer'])} chars")
        if not result["passed"]:
            print(f"         Reason: {result['reason']}")
        elif result["answer"]:
            print(f"         Preview: {result['answer'][:100]}...")
        print()
        results.append(result)

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed  = (datetime.now(timezone.utc) - total_start).total_seconds()
    passed_n = sum(1 for r in results if r["passed"])
    all_pass = passed_n == len(results)

    print("-" * 55)
    print(f"  Questions  : {len(results)}")
    print(f"  Passed     : {passed_n} / {len(results)}")
    print(f"  Total time : {elapsed:.1f}s")
    print("-" * 55)

    if all_pass:
        print("\n  SMOKE TEST PASS")
        print("  Pipeline is healthy — safe to run the full benchmark:")
        print(f"    python benchmarking/run_benchmark.py")
    else:
        failed = [r for r in results if not r["passed"]]
        print(f"\n  SMOKE TEST FAIL  ({len(failed)} question(s) failed)")
        for r in failed:
            print(f"    * {r['question']}")
            if r["reason"]:
                print(f"      {r['reason']}")
        print()
        print("  Common causes:")
        print("    * Vault is empty — ingest a PDF first via the dashboard")
        print("    * OPENAI_API_KEY not set or quota exceeded")
        print("    * API not fully started — wait 15s after docker compose up")

    print()
    return 0 if all_pass else 1


# ── Entry point ────────────────────────────────────────────────────────────────

async def main() -> int:
    parser = argparse.ArgumentParser(
        description="ResearchHub HTTP Smoke Test — quick pipeline health check",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python benchmarking/smoke_test.py\n"
            "  python benchmarking/smoke_test.py --token eyJ...\n"
            "  BENCHMARK_TOKEN=eyJ... python benchmarking/smoke_test.py"
        ),
    )
    parser.add_argument(
        "--url",
        default=os.getenv("API_BASE_URL", "http://localhost:8000/api/v1"),
        help="API base URL (default: API_BASE_URL env var or http://localhost:8000/api/v1)",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("BENCHMARK_TOKEN", ""),
        help="Bearer token.  If omitted, a temporary test user is auto-created.",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip health and vault checks",
    )
    args = parser.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'-' * 55}")
    print("  ResearchHub Smoke Test")
    print(f"  API   : {args.url}")
    print(f"  Time  : {ts}")
    print(f"{'-' * 55}\n")

    # Get or create auth token
    try:
        token, tenant_id = await _get_or_create_token(args.url, args.token)
    except Exception as exc:
        print(f"FATAL: Cannot authenticate — {exc}")
        print("Is the API running?  docker compose up -d")
        return 2

    if not args.skip_preflight:
        if not await preflight(args.url, token):
            return 2

    return await run_smoke_test(args.url, token)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
