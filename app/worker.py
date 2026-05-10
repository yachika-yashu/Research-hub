import os
from arq import create_pool
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
    except:
        redis_settings = RedisSettings(host="localhost", port=6379, database=0)
else:
    redis_settings = RedisSettings(host="localhost", port=6379, database=0)

async def startup(ctx):
    """
    Initialize worker dependencies: Database connection, vector store, etc.
    """
    import asyncio
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

async def process_ingestion_task(ctx, file_content: bytes, filename: str, user_id: int, tenant_id: str, job_id: str):
    """
    Background task to process PDF ingestion.
    Publishes JSON progress events to the Redis channel `ingest:{job_id}`.
    """
    import json
    from app.core.database import get_db, User
    from app.services.ingestion import stream_process_ingestion
    
    redis_client = ctx["redis"]
    channel = f"ingest:{job_id}"
    
    # Retrieve user from DB
    db = next(get_db())
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            await redis_client.publish(channel, json.dumps({"type": "error", "message": "User not found"}))
            return
            
        async for event in stream_process_ingestion(file_content, filename, user, db):
            await redis_client.publish(channel, json.dumps(event))
            
    except Exception as e:
        await redis_client.publish(channel, json.dumps({"type": "error", "message": str(e)}))
    finally:
        db.close()
        # Publish an end marker so subscribers can close connection
        await redis_client.publish(channel, json.dumps({"type": "eof"}))

class WorkerSettings:
    functions = [process_ingestion_task]
    redis_settings = redis_settings
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    job_timeout = 600  # Allow up to 10 minutes for heavy PDF processing
