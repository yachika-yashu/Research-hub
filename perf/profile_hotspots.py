import argparse
import cProfile
import io
import pstats
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run targeted local profiling for Hanuman hotspots.")
    parser.add_argument(
        "--target",
        choices=["search_vdb"],
        required=True,
        help="Which hotspot to profile.",
    )
    parser.add_argument("--query", default="transformer architecture attention", help="Search query")
    parser.add_argument("--tenant-id", default="demo-tenant", help="Tenant id for retrieval profiling")
    parser.add_argument("--limit", type=int, default=5, help="Result limit for retrieval profiling")
    parser.add_argument(
        "--sort",
        default="cumtime",
        choices=["cumtime", "tottime", "calls"],
        help="cProfile sort key",
    )
    parser.add_argument("--top", type=int, default=30, help="Number of rows to print")
    parser.add_argument("--output", type=Path, help="Optional file to save profiler output")
    return parser


async def profile_search_vdb(query: str, tenant_id: str, limit: int) -> None:
    from app.services.vector_store import search_vdb

    await search_vdb(query=query, tenant_id=tenant_id, limit=limit)


def render_stats(profile: cProfile.Profile, sort_key: str, top: int) -> str:
    buffer = io.StringIO()
    stats = pstats.Stats(profile, stream=buffer).strip_dirs().sort_stats(sort_key)
    stats.print_stats(top)
    return buffer.getvalue()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    profile = cProfile.Profile()

    if args.target == "search_vdb":
        import asyncio

        profile.enable()
        asyncio.run(profile_search_vdb(args.query, args.tenant_id, args.limit))
        profile.disable()
    else:
        raise SystemExit(f"Unsupported target: {args.target}")

    output = render_stats(profile, args.sort, args.top)
    print(output)
    if args.output:
        args.output.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
