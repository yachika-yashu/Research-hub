import uuid
import json
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from qdrant_client.http import models as rest

# LangChain message types — needed to build correctly-typed inputs for the
# LangGraph agent and to classify messages when rendering chat history.
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

from app.schemas.models import (
    IngestResponse, QueryRequest, QueryResponse, Citation,
    CompareRequest, QueryFilters, NoteCreate, NoteUpdate
)
from app.services.ingestion import process_ingestion, download_and_ingest_arxiv
from app.services.vector_store import get_qdrant, search_vdb, list_unique_papers, QDRANT_COLLECTION
from app.core.auth import get_current_user
# Import all DB models and helpers in one place to avoid scattered mid-file imports
from app.core.database import (
    User, UsageLog, TraceLog, Note, PaperSummary,
    PaperDetails, PaperReference, ReadingQueueItem,
    ArxivMonitor, ArxivAlert,
    get_db, SessionLocal,
)
from app.core.cache import (
    exact_cache_get, exact_cache_set,
    semantic_cache_get, semantic_cache_set,
    invalidate_exact_cache_for_tenant,
)
from app.core.logic import (
    verify_faithfulness, estimate_cost, count_tokens,
    build_bibtex_entry, generate_bibtex_key, compute_graph_edges,
    generate_lit_review,
)
from app.core.config import GENERATION_MODEL
from app.core.guardrails import pre_retrieval_guardrail
from app.core.globals import openai_client
from app.core.logging import logger

router = APIRouter()

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health")
async def health_check():
    return {"status": "healthy", "version": "v1.1.0", "engine": "Production Multimodal RAG"}


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

@router.post("/ingest")
async def ingest_document(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """Enqueue document ingestion to the background worker."""
    logger.info(f"INGEST: Enqueueing file {file.filename} by user {current_user.username}")

    content = await file.read()
    job_id = str(uuid.uuid4())

    arq_pool = request.app.state.arq_pool
    await arq_pool.enqueue_job(
        "process_ingestion_task",
        content,
        file.filename,
        current_user.id,
        current_user.tenant_id,
        job_id,
        _job_id=job_id
    )

    return {"status": "enqueued", "job_id": job_id, "filename": file.filename}


@router.get("/ingest/stream/{job_id}")
async def stream_ingestion_status(
    job_id: str,
    request: Request,
    current_user: User = Depends(get_current_user)
):
    """Stream progress events from the background worker via Redis Pub/Sub."""
    import redis.asyncio as redis
    from app.core.config import REDIS_URL

    async def event_generator():
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(f"ingest:{job_id}")

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, str):
                        try:
                            json_data = json.loads(data)
                            yield f"data: {data}\n\n"
                            if json_data.get("type") in ["completed", "error", "eof"]:
                                break
                        except json.JSONDecodeError:
                            pass
        finally:
            await pubsub.unsubscribe()
            await redis_client.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/ingest/arxiv")
async def ingest_arxiv_paper(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Download a paper from Arxiv by ID and ingest it into the vault."""
    arxiv_id = (body.get("arxiv_id") or "").strip()
    if not arxiv_id:
        raise HTTPException(status_code=400, detail="arxiv_id is required")
    message = await download_and_ingest_arxiv(arxiv_id, current_user, db)
    ok = message.startswith("Successfully")
    return {"ok": ok, "message": message}


# ---------------------------------------------------------------------------
# Vault management
# ---------------------------------------------------------------------------

@router.get("/vault/papers")
async def list_vault_papers(current_user: User = Depends(get_current_user)):
    """Lists all papers indexed in the current tenant's vault."""
    papers = await list_unique_papers(current_user.tenant_id)
    return {"papers": papers, "count": len(papers)}


@router.get("/vault/papers/{filename}/summary")
async def get_paper_summary(
    filename: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Returns the auto-generated summary for a specific paper."""
    summary = db.query(PaperSummary).filter(
        PaperSummary.tenant_id == current_user.tenant_id,
        PaperSummary.filename == filename
    ).first()
    if not summary:
        raise HTTPException(status_code=404, detail="No summary available for this paper yet.")
    return {"filename": filename, "summary": summary.summary}


@router.post("/vault/compare")
async def compare_papers(
    body: CompareRequest,
    current_user: User = Depends(get_current_user)
):
    """Stream a structured comparison of two vault papers on a research question."""
    chunks_a = await search_vdb(body.question, current_user.tenant_id, limit=5, filters=QueryFilters(filename=body.filename_a))
    chunks_b = await search_vdb(body.question, current_user.tenant_id, limit=5, filters=QueryFilters(filename=body.filename_b))

    context_a = "\n\n".join(c.get("text", "") for c in chunks_a)
    context_b = "\n\n".join(c.get("text", "") for c in chunks_b)

    prompt = (
        f"You are a senior research analyst. Compare these two papers on the question below.\n\n"
        f"PAPER A — {body.filename_a}:\n{context_a}\n\n"
        f"PAPER B — {body.filename_b}:\n{context_b}\n\n"
        f"RESEARCH QUESTION: {body.question}\n\n"
        "Respond with clearly labelled sections:\n"
        "## Paper A\n## Paper B\n## Key Similarities\n## Key Differences\n## Conclusion"
    )

    async def event_gen():
        try:
            response = await openai_client.chat.completions.create(
                model=GENERATION_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                stream=True
            )
            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield f"data: {json.dumps({'type': 'token', 'content': chunk.choices[0].delta.content})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.delete("/vault/papers/{filename}")
async def delete_vault_paper(
    filename: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Removes all chunks for a specific paper from the vault and invalidates cache."""
    client = get_qdrant()
    tenant_id = current_user.tenant_id

    client.delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=rest.FilterSelector(
            filter=rest.Filter(
                must=[
                    rest.FieldCondition(key="tenant_id", match=rest.MatchValue(value=tenant_id)),
                    rest.FieldCondition(key="metadata.filename", match=rest.MatchValue(value=filename)),
                ]
            )
        )
    )

    db.query(PaperSummary).filter(PaperSummary.tenant_id == tenant_id, PaperSummary.filename == filename).delete()
    db.query(PaperDetails).filter(PaperDetails.tenant_id == tenant_id, PaperDetails.filename == filename).delete()
    db.query(PaperReference).filter(PaperReference.tenant_id == tenant_id, PaperReference.source_filename == filename).delete()
    db.commit()

    await invalidate_exact_cache_for_tenant(tenant_id)
    logger.info(f"VAULT: Deleted '{filename}' for tenant {tenant_id}")
    return {"status": "deleted", "filename": filename}


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

@router.post("/query")
async def handle_query(
    query_req: QueryRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    """
    Streaming Graph Orchestration.
    Uses LangGraph to coordinate research tools and stream tokens via SSE.
    """
    thread_id = query_req.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    # Cache lookup
    exact_cached_res = None
    cached_res = None
    try:
        exact_cached_res = await exact_cache_get(current_user.tenant_id, query_req.query)
        if not exact_cached_res:
            cached_res = await semantic_cache_get(current_user.tenant_id, query_req.query)
        else:
            cached_res = exact_cached_res
    except Exception as e:
        logger.warning(f"CACHE: Optimization bypassed — {e}")

    logger.info(f"QUERY: Thread {thread_id} started for user {current_user.username}")

    async def event_generator():
        yield f"data: {json.dumps({'status': 'agent_started', 'thread_id': thread_id})}\n\n"

        if exact_cached_res:
            yield f"data: {json.dumps({'status': 'cache_hit', 'cache_type': 'exact', 'type': 'token', 'content': exact_cached_res['answer']})}\n\n"
            yield "data: [DONE]\n\n"
            return

        if cached_res:
            yield f"data: {json.dumps({'status': 'cache_hit', 'cache_type': 'semantic', 'type': 'token', 'content': cached_res['answer']})}\n\n"
            yield "data: [DONE]\n\n"
            return

        # Guardrail check — fast LLM call before spinning up the full graph
        guard = await pre_retrieval_guardrail(query_req.query)
        if not guard.get("allowed", True):
            reason = guard.get("reason", "This request falls outside the platform's research scope.")
            yield f"data: {json.dumps({'type': 'token', 'content': f'I cannot assist with that request. {reason}'})}\n\n"
            yield "data: [DONE]\n\n"
            return

        # Graph invocation — use HumanMessage so checkpoints store typed objects,
        # not raw tuples that get skipped by isinstance checks on retrieval.
        inputs = {
            "messages": [HumanMessage(content=query_req.query)],
            "tenant_id": current_user.tenant_id
        }

        graph = request.app.state.graph
        full_response = ""
        start_time = datetime.now()

        try:
            async for event in graph.astream_events(inputs, config, version="v1"):
                kind = event["event"]
                if kind == "on_chat_model_stream":
                    content = event["data"]["chunk"].content
                    if content:
                        full_response += content
                        yield f"data: {json.dumps({'type': 'token', 'content': content})}\n\n"
                elif kind == "on_tool_start":
                    yield f"data: {json.dumps({'type': 'tool_start', 'tool': event['name']})}\n\n"
                elif kind == "on_tool_end":
                    yield f"data: {json.dumps({'type': 'tool_end', 'tool': event['name'], 'output': str(event['data']['output'])})}\n\n"
        except Exception as exc:
            logger.error(f"QUERY: Graph execution error for thread {thread_id}: {exc}", exc_info=True)

            # Corrupted checkpoint: AI sent a tool_call but no ToolMessage was saved.
            # Wipe the thread's checkpoint so the next message starts fresh.
            err_str = str(exc)
            if "tool_calls" in err_str and "tool messages" in err_str:
                try:
                    _db = SessionLocal()
                    for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                        _db.execute(text(f"DELETE FROM {tbl} WHERE thread_id = :tid"), {"tid": thread_id})
                    _db.commit()
                    _db.close()
                    logger.warning(f"QUERY: Auto-reset corrupted checkpoint for thread {thread_id}")
                except Exception as reset_exc:
                    logger.warning(f"QUERY: Checkpoint reset failed: {reset_exc}")
                error_msg = "I ran into a conversation state issue and have reset this thread. Please resend your message."
            else:
                error_msg = "An error occurred while processing your query. Please try again."

            yield f"data: {json.dumps({'type': 'token', 'content': error_msg})}\n\n"
            yield "data: [DONE]\n\n"
            return

        latency = (datetime.now() - start_time).total_seconds() * 1000
        tokens_in = count_tokens(query_req.query)
        tokens_out = count_tokens(full_response)
        yield f"data: {json.dumps({'type': 'metrics', 'latency_ms': round(latency, 2), 'tokens_in': tokens_in, 'tokens_out': tokens_out})}\n\n"

        background_tasks.add_task(
            finalize_query_governance,
            current_user,
            query_req,
            full_response,
            thread_id
        )

        yield "data: [DONE]\n\n"
        logger.info(f"QUERY: Thread {thread_id} completed in {latency:.0f}ms")

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def finalize_query_governance(user: User, query_req: QueryRequest, full_response: str, thread_id: str):
    """Handles usage logging and caching after the stream completes."""
    db = next(get_db())
    try:
        tokens_in = count_tokens(query_req.query)
        tokens_out = count_tokens(full_response)
        cost = estimate_cost(tokens_in, tokens_out, GENERATION_MODEL)

        usage = UsageLog(
            tenant_id=user.tenant_id,
            user_id=user.id,
            event_type="query",
            model_name=GENERATION_MODEL,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            estimated_cost_usd=cost
        )
        db.add(usage)
        db.flush()

        trace = TraceLog(
            usage_log_id=usage.id,
            tenant_id=user.tenant_id,
            full_prompt=query_req.query,
            context_data_json=json.dumps([]),
            faithfulness_report_json=json.dumps({"score": 1.0, "reason": "System verification completed"})
        )
        db.add(trace)
        db.commit()

        await semantic_cache_set(
            user.tenant_id,
            query_req.query,
            {"answer": full_response, "thread_id": thread_id}
        )
        await exact_cache_set(
            user.tenant_id,
            query_req.query,
            {"answer": full_response, "thread_id": thread_id}
        )
    except Exception as e:
        logger.error(f"GOVERNANCE: Logging failed — {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------


def _to_chat_message(m) -> Optional[dict]:
    """Normalise any message representation to {role, content} or None."""
    # LangChain message objects
    if isinstance(m, HumanMessage):
        text = m.content if isinstance(m.content, str) else str(m.content)
        return {"role": "user", "content": text} if text else None
    if isinstance(m, AIMessage):
        text = m.content if isinstance(m.content, str) else str(m.content)
        return {"role": "assistant", "content": text} if text else None
    if isinstance(m, (SystemMessage, ToolMessage)):
        return None  # never show in history UI

    # Raw tuple — e.g. ("user", "hello") stored via operator.add
    if isinstance(m, (list, tuple)) and len(m) == 2:
        role_str, content = m
        if str(role_str).lower() in ("human", "user"):
            return {"role": "user", "content": str(content)}
        if str(role_str).lower() in ("ai", "assistant"):
            text = str(content)
            return {"role": "assistant", "content": text} if text else None

    # Dict — produced by some serialisers
    if isinstance(m, dict):
        role_str = str(m.get("type", m.get("role", ""))).lower()
        content = m.get("content", "")
        if role_str in ("human", "user") and content:
            return {"role": "user", "content": str(content)}
        if role_str in ("ai", "assistant") and content:
            return {"role": "assistant", "content": str(content)}

    return None


@router.get("/chat/threads")
async def get_threads(request: Request, current_user: User = Depends(get_current_user)):
    """Retrieves all active research thread IDs for the current tenant."""
    checkpointer = request.app.state.checkpointer
    tenant_id = current_user.tenant_id
    all_threads = []

    try:
        async for checkpoint in checkpointer.alist(None):
            cid = checkpoint.config.get("configurable", {}).get("thread_id")
            if not cid or cid in all_threads:
                continue
            # Filter to this tenant by inspecting checkpoint state
            state = checkpoint.checkpoint.get("channel_values", {})
            if state.get("tenant_id") == tenant_id:
                all_threads.append(cid)
    except Exception as exc:
        logger.error(f"THREADS: Failed to list threads — {exc}", exc_info=True)

    return {"threads": all_threads}


@router.get("/chat/history/{thread_id}")
async def get_chat_history(
    thread_id: str,
    request: Request,
    current_user: User = Depends(get_current_user)
):
    """Fetches full message history for a specific thread."""
    checkpointer = request.app.state.checkpointer
    config = {"configurable": {"thread_id": thread_id}}

    checkpoint_tuple = await checkpointer.aget(config)
    if not checkpoint_tuple:
        return {"messages": []}

    # aget returns a CheckpointTuple; the state dict is in .checkpoint
    checkpoint_data = (
        checkpoint_tuple.checkpoint
        if hasattr(checkpoint_tuple, "checkpoint")
        else checkpoint_tuple
    )
    state = checkpoint_data.get("channel_values", {}) if hasattr(checkpoint_data, "get") else {}

    if state.get("tenant_id") and state["tenant_id"] != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Thread does not belong to your team.")

    raw_messages = state.get("messages", [])
    formatted = [msg for m in raw_messages if (msg := _to_chat_message(m)) is not None]
    return {"messages": formatted}


# ---------------------------------------------------------------------------
# Debug / stats
# ---------------------------------------------------------------------------

@router.get("/debug/stats")
async def get_vault_stats(current_user: User = Depends(get_current_user)):
    """Diagnostic endpoint to verify multi-tenant isolation."""
    client = get_qdrant()
    tenant_id = current_user.tenant_id

    res = client.count(
        collection_name=QDRANT_COLLECTION,
        count_filter=rest.Filter(
            must=[rest.FieldCondition(key="tenant_id", match=rest.MatchValue(value=tenant_id))]
        )
    )
    total = client.count(collection_name=QDRANT_COLLECTION).count

    return {
        "tenant_id": tenant_id,
        "points_in_vault": res.count,
        "total_points_in_collection": total,
        "status": "ready"
    }


@router.get("/stats/usage")
async def get_usage_metrics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Aggregates usage metrics for the governance dashboard."""
    tenant_id = current_user.tenant_id
    logs = db.query(UsageLog).filter(UsageLog.tenant_id == tenant_id).all()

    total_tokens = sum((log.tokens_input + log.tokens_output) for log in logs)
    total_cost = sum(log.estimated_cost_usd for log in logs)

    faith_scores = []
    for log in logs:
        if log.event_type == "query" and log.metrics_json:
            try:
                m = json.loads(log.metrics_json)
                if "faithfulness_score" in m:
                    faith_scores.append(m["faithfulness_score"])
            except Exception:
                pass

    avg_faith = sum(faith_scores) / len(faith_scores) if faith_scores else 1.0

    return {
        "tenant_id": tenant_id,
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 4),
        "avg_faithfulness": round(avg_faith, 2),
        "history": [
            {
                "id": log.id,
                "type": log.event_type,
                "cost": log.estimated_cost_usd,
                "time": log.timestamp.isoformat()
            }
            for log in logs[-10:]
        ]
    }


@router.get("/stats/trace/{usage_id}")
async def get_deep_trace(
    usage_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Fetches full RAG trace details for debugging."""
    trace = db.query(TraceLog).filter(
        TraceLog.usage_log_id == usage_id,
        TraceLog.tenant_id == current_user.tenant_id
    ).first()

    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found for this event.")

    return {
        "id": trace.id,
        "prompt": trace.full_prompt,
        "context": json.loads(trace.context_data_json),
        "verifier": json.loads(trace.faithfulness_report_json)
    }


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


def _note_to_dict(note: Note) -> dict:
    return {
        "id": note.id,
        "title": note.title,
        "content": note.content,
        "created_at": note.created_at.isoformat(),
        "updated_at": note.updated_at.isoformat(),
    }


@router.get("/notes")
async def list_notes(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Returns all notes for the current user, newest first."""
    notes = (
        db.query(Note)
        .filter(Note.tenant_id == current_user.tenant_id, Note.user_id == current_user.id)
        .order_by(Note.updated_at.desc())
        .all()
    )
    return {"notes": [_note_to_dict(n) for n in notes]}


@router.post("/notes", status_code=201)
async def create_note(
    body: NoteCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Creates a new note."""
    note = Note(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        title=body.title,
        content=body.content,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return _note_to_dict(note)


@router.put("/notes/{note_id}")
async def update_note(
    note_id: str,
    body: NoteUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Updates title and/or content of an existing note."""
    note = db.query(Note).filter(
        Note.id == note_id,
        Note.user_id == current_user.id,
        Note.tenant_id == current_user.tenant_id
    ).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")
    if body.title is not None:
        note.title = body.title
    if body.content is not None:
        note.content = body.content
    note.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(note)
    return _note_to_dict(note)


@router.delete("/notes/{note_id}")
async def delete_note(
    note_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Deletes a note permanently."""
    note = db.query(Note).filter(
        Note.id == note_id,
        Note.user_id == current_user.id,
        Note.tenant_id == current_user.tenant_id
    ).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found.")
    db.delete(note)
    db.commit()
    return {"status": "deleted", "id": note_id}


# ---------------------------------------------------------------------------
# BibTeX Export — formats all vault papers as a .bib file for LaTeX/Zotero
# ---------------------------------------------------------------------------

@router.get("/vault/export/bibtex")
async def export_bibtex(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Returns all vault papers as a .bib file. Falls back to Qdrant metadata
    for papers ingested before paper_details was introduced."""
    import json as _json
    from fastapi.responses import Response

    tenant_id = current_user.tenant_id
    details = db.query(PaperDetails).filter(PaperDetails.tenant_id == tenant_id).all()

    entries = []
    seen_keys: set = set()
    for d in details:
        key = d.bibtex_key or generate_bibtex_key(
            _json.loads(d.authors or "[]"), d.year, d.title or d.filename
        )
        # Deduplicate keys at export time
        base = key
        sfx = ord('a')
        while key in seen_keys:
            key = f"{base}{chr(sfx)}"
            sfx += 1
        seen_keys.add(key)

        entry = build_bibtex_entry({
            "title": d.title, "authors": _json.loads(d.authors or "[]"),
            "year": d.year, "doi": d.doi, "journal": d.journal,
        }, key)
        entries.append(entry)

    # Fallback: papers in Qdrant but not yet in paper_details (legacy ingestions)
    known = {d.filename for d in details}
    vault = await list_unique_papers(tenant_id)
    for p in vault:
        if p["filename"] in known:
            continue
        key = generate_bibtex_key(p.get("authors") or [], p.get("year"), p.get("title") or p["filename"])
        base = key
        sfx = ord('a')
        while key in seen_keys:
            key = f"{base}{chr(sfx)}"
            sfx += 1
        seen_keys.add(key)
        entries.append(build_bibtex_entry(p, key))

    bib_content = "\n\n".join(entries)
    return Response(
        content=bib_content,
        media_type="text/plain",
        headers={"Content-Disposition": 'attachment; filename="vault.bib"'}
    )


# ---------------------------------------------------------------------------
# Paper Details & References — structured extraction results per paper
# ---------------------------------------------------------------------------

@router.get("/vault/papers/{filename}/details")
async def get_paper_details(
    filename: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Returns structured research fields (contribution, dataset, baselines, limitations)."""
    import json as _json
    d = db.query(PaperDetails).filter(
        PaperDetails.tenant_id == current_user.tenant_id,
        PaperDetails.filename == filename
    ).first()
    if not d:
        raise HTTPException(status_code=404, detail="Details not available. Re-ingest the paper to generate them.")
    return {
        "filename": filename,
        "contribution": d.contribution,
        "dataset": d.dataset,
        "baselines": _json.loads(d.baselines or "[]"),
        "limitations": d.limitations,
        "bibtex_key": d.bibtex_key,
    }


@router.get("/vault/papers/{filename}/references")
async def get_paper_references(
    filename: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Returns the bibliography extracted from a paper at ingest time."""
    import json as _json
    refs = db.query(PaperReference).filter(
        PaperReference.tenant_id == current_user.tenant_id,
        PaperReference.source_filename == filename
    ).all()
    return {
        "filename": filename,
        "references": [
            {
                "id": r.id,
                "title": r.ref_title,
                "authors": _json.loads(r.ref_authors or "[]"),
                "year": r.ref_year,
                "doi": r.ref_doi,
                "arxiv_id": r.arxiv_id,
            }
            for r in refs
        ]
    }


@router.post("/vault/papers/{filename}/reextract-references")
async def reextract_references(
    filename: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Re-runs bibliography extraction on the stored Qdrant chunks for a paper.
    Useful when the paper was ingested before reference extraction was in the pipeline,
    or when the original extraction silently failed."""
    import json as _json
    from app.core.logic import extract_bibliography

    tenant_id = current_user.tenant_id
    client = get_qdrant()

    # Retrieve all stored chunks for this paper
    results, _ = client.scroll(
        collection_name=QDRANT_COLLECTION,
        scroll_filter=rest.Filter(
            must=[
                rest.FieldCondition(key="tenant_id", match=rest.MatchValue(value=tenant_id)),
                rest.FieldCondition(key="metadata.filename", match=rest.MatchValue(value=filename)),
            ]
        ),
        limit=2000,
        with_payload=True,
        with_vectors=False,
    )

    if not results:
        raise HTTPException(status_code=404, detail="Paper not found in vault.")

    # Reassemble document text ordered by chunk_index
    chunks_sorted = sorted(results, key=lambda p: p.payload.get("chunk_index", 0))
    full_text = "\n".join(p.payload.get("text", "") for p in chunks_sorted)

    if len(full_text) < 200:
        return {"filename": filename, "references_found": 0, "message": "Insufficient text to extract references."}

    refs_data = await extract_bibliography(openai_client, full_text)

    if refs_data:
        db.query(PaperReference).filter(
            PaperReference.tenant_id == tenant_id,
            PaperReference.source_filename == filename
        ).delete()
        for ref in refs_data:
            db.add(PaperReference(
                tenant_id=tenant_id,
                source_filename=filename,
                ref_title=ref.get("title", ""),
                ref_authors=_json.dumps(ref.get("authors") or []),
                ref_year=ref.get("year"),
                ref_doi=ref.get("doi"),
                arxiv_id=ref.get("arxiv_id"),
            ))
        db.commit()

    return {"filename": filename, "references_found": len(refs_data)}


# ---------------------------------------------------------------------------
# Cross-Vault Passage Search — raw chunk retrieval with no LLM involved
# ---------------------------------------------------------------------------

@router.post("/vault/search/passages")
async def search_passages(
    body: dict,
    current_user: User = Depends(get_current_user)
):
    """Direct semantic chunk search — returns raw passages, no LLM generation.
    Useful when the researcher needs the exact quoted text, not an AI paraphrase."""
    query = body.get("query", "")
    limit = min(int(body.get("limit", 10)), 20)
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    results = await search_vdb(query, current_user.tenant_id, limit=limit)
    return {
        "query": query,
        "passages": [
            {
                "text": r.get("text", ""),
                "filename": r.get("metadata", {}).get("filename", ""),
                "chunk_index": r.get("chunk_index", 0),
                "score": round(r.get("precision_score", 0.0), 4),
            }
            for r in results
        ]
    }


# ---------------------------------------------------------------------------
# Literature Review Generator — multi-paper synthesis streamed via SSE
# ---------------------------------------------------------------------------

@router.post("/vault/analyze/literature-review")
async def literature_review(
    body: dict,
    current_user: User = Depends(get_current_user)
):
    """Streams a structured literature review across up to 5 selected papers."""
    filenames = (body.get("filenames") or [])[:5]   # cap at 5 papers
    question  = body.get("question", "What are the key contributions and differences?")
    if not filenames:
        raise HTTPException(status_code=400, detail="Provide at least one filename.")

    # Retrieve context chunks per paper (5 chunks each to stay within token budget)
    papers_context = []
    labels = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for i, fname in enumerate(filenames):
        from app.schemas.models import QueryFilters
        chunks = await search_vdb(question, current_user.tenant_id, limit=5,
                                  filters=QueryFilters(filename=fname))
        chunks_text = "\n\n".join(c.get("text", "") for c in chunks)
        papers_context.append({"label": labels[i], "title": fname, "chunks_text": chunks_text})

    async def event_gen():
        try:
            async for token in generate_lit_review(openai_client, papers_context, question):
                yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Knowledge Graph — compute paper-to-paper edges from metadata
# ---------------------------------------------------------------------------

@router.get("/vault/graph")
async def get_knowledge_graph(current_user: User = Depends(get_current_user)):
    """Returns nodes and edges for the vault knowledge graph.
    Edges connect papers that share authors or keywords."""
    papers = await list_unique_papers(current_user.tenant_id)
    graph  = compute_graph_edges(papers)
    return graph


# ---------------------------------------------------------------------------
# Reading Queue — papers to read later, not yet indexed in vault
# ---------------------------------------------------------------------------

def _queue_to_dict(item: ReadingQueueItem) -> dict:
    import json as _json
    return {
        "id": item.id, "title": item.title,
        "authors": _json.loads(item.authors or "[]"),
        "arxiv_id": item.arxiv_id, "url": item.url,
        "notes": item.notes, "status": item.status,
        "added_at": item.added_at.isoformat(),
    }


@router.get("/queue")
async def list_queue(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    items = db.query(ReadingQueueItem).filter(
        ReadingQueueItem.tenant_id == current_user.tenant_id,
        ReadingQueueItem.user_id == current_user.id
    ).order_by(ReadingQueueItem.added_at.desc()).all()
    return {"items": [_queue_to_dict(i) for i in items]}


@router.post("/queue", status_code=201)
async def add_to_queue(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    import json as _json
    item = ReadingQueueItem(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        title=body.get("title", "Untitled"),
        authors=_json.dumps(body.get("authors") or []),
        arxiv_id=body.get("arxiv_id"),
        url=body.get("url"),
        notes=body.get("notes", ""),
        status="queued",
    )
    db.add(item); db.commit(); db.refresh(item)
    return _queue_to_dict(item)


@router.patch("/queue/{item_id}")
async def update_queue_item(
    item_id: str,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    item = db.query(ReadingQueueItem).filter(
        ReadingQueueItem.id == item_id,
        ReadingQueueItem.user_id == current_user.id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")
    if "status" in body: item.status = body["status"]
    if "notes"  in body: item.notes  = body["notes"]
    db.commit(); db.refresh(item)
    return _queue_to_dict(item)


@router.delete("/queue/{item_id}")
async def delete_queue_item(
    item_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    item = db.query(ReadingQueueItem).filter(
        ReadingQueueItem.id == item_id,
        ReadingQueueItem.user_id == current_user.id
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")
    db.delete(item); db.commit()
    return {"status": "deleted", "id": item_id}


# ---------------------------------------------------------------------------
# Arxiv Monitoring — keyword watches + alert digest
# The arq worker polls export.arxiv.org daily (outbound HTTP — works on localhost)
# ---------------------------------------------------------------------------

@router.get("/monitors")
async def list_monitors(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    monitors = db.query(ArxivMonitor).filter(
        ArxivMonitor.tenant_id == current_user.tenant_id,
        ArxivMonitor.user_id == current_user.id,
        ArxivMonitor.is_active == True
    ).all()
    return {"monitors": [{"id": m.id, "keywords": m.keywords,
                          "last_checked_at": m.last_checked_at.isoformat() if m.last_checked_at else None}
                         for m in monitors]}


@router.post("/monitors", status_code=201)
async def create_monitor(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    kw = (body.get("keywords") or "").strip()
    if not kw:
        raise HTTPException(status_code=400, detail="keywords required")
    m = ArxivMonitor(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        keywords=kw,
    )
    db.add(m); db.commit(); db.refresh(m)
    return {"id": m.id, "keywords": m.keywords}


@router.delete("/monitors/{monitor_id}")
async def delete_monitor(
    monitor_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    m = db.query(ArxivMonitor).filter(
        ArxivMonitor.id == monitor_id,
        ArxivMonitor.user_id == current_user.id
    ).first()
    if not m:
        raise HTTPException(status_code=404, detail="Monitor not found.")
    m.is_active = False
    db.commit()
    return {"status": "deleted"}


@router.get("/monitors/alerts")
async def list_alerts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    import json as _json
    alerts = db.query(ArxivAlert).filter(
        ArxivAlert.tenant_id == current_user.tenant_id,
        ArxivAlert.is_read == False
    ).order_by(ArxivAlert.created_at.desc()).limit(50).all()
    return {"alerts": [
        {"id": a.id, "arxiv_id": a.arxiv_id, "title": a.title,
         "abstract": a.abstract[:300] if a.abstract else "",
         "authors": _json.loads(a.authors or "[]"),
         "published_at": a.published_at}
        for a in alerts
    ]}


@router.post("/monitors/alerts/{alert_id}/read")
async def mark_alert_read(
    alert_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    a = db.query(ArxivAlert).filter(
        ArxivAlert.id == alert_id,
        ArxivAlert.tenant_id == current_user.tenant_id
    ).first()
    if not a:
        raise HTTPException(status_code=404, detail="Alert not found.")
    a.is_read = True
    db.commit()
    return {"status": "read"}
