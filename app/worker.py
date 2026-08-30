import os
from arq import cron
from arq.connections import RedisSettings
from dotenv import load_dotenv

# Ensure env vars are loaded
load_dotenv()

# Setup Redis settings
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# Parse redis url
if "://" in REDIS_URL:
    # simple parsing for arq: redis://localhost:6379/0
    try:
        parts = REDIS_URL.split("redis://")[1].split("/")
        host_port = parts[0].split(":")
        host = host_port[0]
        port = int(host_port[1]) if len(host_port) > 1 else 6379
        db = int(parts[1]) if len(parts) > 1 else 0
        redis_settings = RedisSettings(host=host, port=port, database=db)
    except Exception:
        redis_settings = RedisSettings(host="localhost", port=6379, database=0)
else:
    redis_settings = RedisSettings(host="localhost", port=6379, database=0)

async def startup(ctx):
    """
    Initialize worker dependencies: Database connection, vector store, etc.
    """
    from app.services.vector_store import init_db
    from app.core.database import init_db as init_user_db
    import redis.asyncio as redis

    await init_db()
    init_user_db()

    # Create an async redis client for publishing progress updates
    ctx["redis"] = redis.from_url(REDIS_URL, decode_responses=True)

async def shutdown(ctx):
    if "redis" in ctx:
        await ctx["redis"].close()

# Progress events live in a Redis Stream for INGEST_STREAM_TTL seconds so a
# subscriber that connects after the worker has already started can replay
# everything from the beginning. Pub/Sub cannot do this: it drops any message
# published while no subscriber is attached, which silently lost the early
# progress events (and sometimes the terminal one) on fast ingestions.
INGEST_STREAM_TTL = 3600


async def process_ingestion_task(ctx, file_content: bytes, filename: str, user_id: int, tenant_id: str, job_id: str):
    """
    Background task to process PDF ingestion.
    Appends JSON progress events to the Redis stream `ingest:{job_id}`.
    """
    import json
    from app.core.database import get_db, User
    from app.services.ingestion import stream_process_ingestion

    redis_client = ctx["redis"]
    stream = f"ingest:{job_id}"

    async def emit(event: dict):
        await redis_client.xadd(stream, {"data": json.dumps(event)})

    # Retrieve user from DB
    db = next(get_db())
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            await emit({"type": "error", "message": "User not found"})
            return

        async for event in stream_process_ingestion(file_content, filename, user, db):
            await emit(event)

    except Exception as e:
        import traceback
        await emit({"type": "error", "message": f"{type(e).__name__}: {e}\n{traceback.format_exc()}"})
    finally:
        db.close()
        # End marker so subscribers can close the connection, then expire the
        # whole stream so completed jobs do not accumulate in Redis.
        await emit({"type": "eof"})
        await redis_client.expire(stream, INGEST_STREAM_TTL)

async def check_arxiv_monitors_task(ctx):
    """
    Cron job: runs once daily (07:00 UTC) to poll Arxiv for new papers matching
    each active monitor's keywords. Outbound HTTP only — no webhook, no public URL.
    New papers are stored as ArxivAlert rows; duplicates (same arxiv_id + monitor_id)
    are silently skipped via INSERT conflict handling.
    """
    import json
    import arxiv
    from datetime import datetime, timezone
    from app.core.database import SessionLocal, ArxivMonitor, ArxivAlert

    db = SessionLocal()
    try:
        monitors = db.query(ArxivMonitor).filter(ArxivMonitor.is_active.is_(True)).all()
        client = arxiv.Client()

        for monitor in monitors:
            try:
                search = arxiv.Search(
                    query=monitor.keywords,
                    max_results=20,
                    sort_by=arxiv.SortCriterion.SubmittedDate,
                    sort_order=arxiv.SortOrder.Descending,
                )
                cutoff = monitor.last_checked_at or datetime(2000, 1, 1, tzinfo=timezone.utc)
                if cutoff.tzinfo is None:
                    cutoff = cutoff.replace(tzinfo=timezone.utc)

                # Collect existing arxiv_ids for this monitor to deduplicate
                existing_ids = {
                    row.arxiv_id
                    for row in db.query(ArxivAlert.arxiv_id)
                        .filter(ArxivAlert.monitor_id == monitor.id)
                        .all()
                }

                for result in client.results(search):
                    published = result.published
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                    if published <= cutoff:
                        break  # Results are newest-first; stop when we reach old papers

                    short_id = result.entry_id.split("/abs/")[-1]
                    if short_id in existing_ids:
                        continue

                    db.add(ArxivAlert(
                        monitor_id=monitor.id,
                        tenant_id=monitor.tenant_id,
                        arxiv_id=short_id,
                        title=result.title,
                        abstract=result.summary,
                        authors=json.dumps([a.name for a in result.authors]),
                        published_at=result.published.isoformat(),
                    ))
                    existing_ids.add(short_id)

                monitor.last_checked_at = datetime.now(timezone.utc)
            except Exception as e:
                # Non-fatal: one monitor failing should not block others
                print(f"ARXIV_MONITOR: Failed for monitor {monitor.id} — {e}")

        db.commit()
    except Exception as e:
        print(f"ARXIV_MONITOR: Cron task failed — {e}")
        db.rollback()
    finally:
        db.close()


class WorkerSettings:
    functions = [process_ingestion_task]
    # Run Arxiv monitor check every day at 07:00 UTC.
    # Uses arq's cron scheduler — the worker process must be running for this to fire.
    cron_jobs = [cron(check_arxiv_monitors_task, hour=7, minute=0)]
    redis_settings = redis_settings
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    job_timeout = 600  # Allow up to 10 minutes for heavy PDF processing
