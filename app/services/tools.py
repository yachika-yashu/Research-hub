from typing import Optional
from langchain_core.tools import tool
from langchain_community.utilities import ArxivAPIWrapper
from langchain_experimental.utilities import PythonREPL
from app.services.vector_store import search_vdb
from app.core.config import API_PUBLIC_URL

# Initialize utilities
arxiv_wrapper = ArxivAPIWrapper()
python_repl = PythonREPL()

@tool
def arxiv_search_tool(query: str) -> str:
    """
    Search Arxiv for research papers.
    Use this to find papers by title, author, or topic when the local vault doesn't have the answer.
    Returns paper summaries, titles, and IDs.
    """
    return arxiv_wrapper.run(query)

@tool
def python_repl_tool(code: str) -> str:
    """
    A Python shell. Use this to execute python commands.
    The input should be a valid python command.
    Useful for data analysis, complex math, or generating plots (though plots won't be visible, data will).
    """
    return python_repl.run(code)

@tool
async def rag_tool(query: str, tenant_id: str, limit: int = 8, filename: Optional[str] = None) -> str:
    """
    Search the local research vault (internal database) for summaries, technical details, and full paper content.
    This is the EXCLUSIVE tool for accessing papers the user has uploaded to their workspace ({tenant_id}).
    If the user asks for a summary, query='summary of the paper'.
    If the user refers to a specific file (e.g. 'the paper I just uploaded'), provide its filename.
    Returns text chunks and citations.
    """
    from app.schemas.models import QueryFilters
    from app.services.vector_store import get_qdrant, QDRANT_COLLECTION
    from qdrant_client.http import models as rest

    # Visual queries need broader search: diagrams/figures are rarely described
    # with the exact query keywords, so we boost depth and add structural terms
    # to improve the chance of retrieving image-containing chunks.
    # "architecture" is included because ML papers always have architecture diagrams.
    visual_keywords = [
        "figure", "diagram", "table", "graph", "chart",
        "map", "illustration", "architecture", "plot", "image", "show", "visual"
    ]
    is_visual_query = any(k in query.lower() for k in visual_keywords)

    adj_limit = limit
    adj_query = query
    if is_visual_query:
        adj_limit = 15
        adj_query = f"Research {query} Figure Table Diagram Architecture"

    filters = QueryFilters(filename=filename) if filename else None
    results = await search_vdb(adj_query, tenant_id, limit=adj_limit, filters=filters)
    if not results:
        return f"No relevant information found in the local research vault for {filename or 'the workspace'}."

    # Secondary pass: if none of the top results contain image markdown, fetch
    # image-bearing chunks explicitly so they are never silently omitted.
    # The reranker can rank text-only chunks above image chunks for factual
    # queries even when the image chunk is the most relevant visual answer.
    has_images = any("![Figure]" in r.get("text", "") for r in results)
    if not has_images:
        try:
            client = get_qdrant()
            img_filter_conditions = [
                rest.FieldCondition(key="tenant_id", match=rest.MatchValue(value=tenant_id)),
            ]
            if filename:
                img_filter_conditions.append(
                    rest.FieldCondition(key="metadata.filename", match=rest.MatchValue(value=filename))
                )
            img_scroll, _ = client.scroll(
                collection_name=QDRANT_COLLECTION,
                scroll_filter=rest.Filter(must=img_filter_conditions),
                limit=200,
                with_payload=True,
                with_vectors=False,
            )
            # Append up to 3 image chunks that aren't already in results
            existing_ids = {r.get("chunk_id") for r in results}
            appended = 0
            for point in img_scroll:
                text = point.payload.get("text", "")
                if "![Figure]" in text and point.payload.get("chunk_id") not in existing_ids:
                    results.append(point.payload)
                    appended += 1
                    if appended >= 3:
                        break
        except Exception:
            pass  # secondary pass is best-effort, never block the main response

    formatted_context = f"--- LOCAL VAULT CONTEXT ({filename or 'Global'}) ---\n"
    for i, hit in enumerate(results):
        text = hit['text']
        # Legacy chunks stored image path in media_url instead of inline markdown.
        # Reconstruct the full URL so the LLM receives a renderable image tag.
        media = hit.get('media_url')
        if media:
            image_url = f"{API_PUBLIC_URL}{media}"
            text += f"\n\n![Figure]({image_url})"
        formatted_context += f"[{i}] {text}\n"
    return formatted_context

@tool
async def auto_ingest_paper_tool(arxiv_id: str, tenant_id: str) -> str:
    """
    Automatically downloads a paper from Arxiv and indexes it into the local research vault.
    Use this after finding a relevant paper via arxiv_search_tool that the user wants to 'add' to their vault.
    arxiv_id should be a string like '2305.10601'.
    """
    from app.services.ingestion import download_and_ingest_arxiv
    from app.core.database import SessionLocal, User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.tenant_id == tenant_id).first()
        if not user:
            return f"Error: No user found for tenant {tenant_id}"

        result = await download_and_ingest_arxiv(arxiv_id, user, db)
        return result
    finally:
        db.close()

@tool
async def list_vault_papers_tool(tenant_id: str) -> str:
    """
    Lists all papers currently stored in your research vault ({tenant_id}).
    Use this if you are unsure which paper the user is referring to or to see the most recently uploaded files.
    Returns filenames, titles, and ingestion timestamps.
    """
    from app.services.vector_store import list_unique_papers
    papers = await list_unique_papers(tenant_id)
    if not papers:
        return "The research vault is currently empty."

    output = "--- RESEARCH VAULT CATALOG ---\n"
    for p in papers:
        output += f"- {p['filename']} (Title: {p['title']}) | Uploaded: {p['ingested_at']}\n"
    return output
