import streamlit as st
import httpx
import os
import asyncio
import urllib.parse
import json
from datetime import datetime
from httpx_sse import aconnect_sse

# --- PAGE CONFIG ---
st.set_page_config(
    page_title="ResearchHub",
    page_icon="🧬",
    layout="wide",
)

# --- CONFIGURATION ---
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")

# --- CUSTOM CSS ---
st.markdown("""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600&display=swap" rel="stylesheet">
    <style>
    html, body, [data-testid="stSidebar"] {
        font-family: 'Outfit', sans-serif !important;
        background-color: #050505 !important;
    }
    .main { background: radial-gradient(circle at top right, #11111d 0%, #050505 100%); }
    [data-testid="stSidebar"] {
        background-color: rgba(10, 10, 15, 0.7) !important;
        backdrop-filter: blur(12px);
        border-right: 1px solid rgba(255, 255, 255, 0.05);
    }
    .chip-btn button {
        background: rgba(99,102,241,0.12) !important;
        border: 1px solid rgba(99,102,241,0.3) !important;
        border-radius: 20px !important;
        color: #a5b4fc !important;
        font-size: 0.82rem !important;
    }
    .chip-btn button:hover {
        background: rgba(99,102,241,0.25) !important;
        border-color: #6366f1 !important;
    }
    </style>
""", unsafe_allow_html=True)

# --- SESSION STATE ---
for k, v in [
    ("auth_token", None), ("user_info", {}), ("messages", []),
    ("thread_id", None), ("filter_paper", None), ("highlight_mode", False),
    ("passage_mode", False), ("passage_results", []),
    ("lit_review_result", ""), ("graph_data", None),
    ("chip_query", None), ("note_selected_id", None), ("notes_cache", None),
]:
    if k not in st.session_state:
        st.session_state[k] = v


# ═══════════════════════════════════════════════════════════════════════════════
# API HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_headers():
    if st.session_state.auth_token:
        return {"Authorization": f"Bearer {st.session_state.auth_token}"}
    return {}

def _error_msg(res: dict, fallback: str) -> str:
    detail = res.get("detail", fallback)
    if isinstance(detail, list):
        return "; ".join(e.get("msg", str(e)) for e in detail)
    return str(detail) if detail else fallback

def format_apa_citation(paper: dict) -> str:
    authors = paper.get("authors") or []
    year    = paper.get("year") or "n.d."
    title   = paper.get("title") or "Untitled"
    journal = paper.get("journal") or ""
    doi     = paper.get("doi") or ""

    if not authors:          author_str = "Unknown Author"
    elif len(authors) == 1:  author_str = authors[0]
    elif len(authors) <= 3:  author_str = ", ".join(authors[:-1]) + ", & " + authors[-1]
    else:                    author_str = authors[0] + " et al."

    cite = f"{author_str} ({year}). {title}."
    if journal: cite += f" *{journal}*."
    if doi:     cite += f" https://doi.org/{doi}"
    return cite

def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

# --- Auth ---
async def register_user(username, password, team_code):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{API_BASE_URL}/auth/register",
                             json={"username": username, "password": password, "team_code": team_code})
            try:    return r.json(), r.status_code
            except: return {"detail": r.text or f"HTTP {r.status_code}"}, r.status_code
    except Exception as e:
        return {"detail": str(e)}, 500

async def login_user(username, password):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{API_BASE_URL}/auth/token",
                             data={"username": username, "password": password})
            try:    return r.json(), r.status_code
            except: return {"detail": r.text or f"HTTP {r.status_code}"}, r.status_code
    except Exception as e:
        return {"detail": str(e)}, 500

# --- Vault ---
async def fetch_vault_papers():
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{API_BASE_URL}/vault/papers", headers=get_headers())
            return r.json().get("papers", [])
    except: return []

async def delete_vault_paper(filename: str) -> bool:
    try:
        enc = urllib.parse.quote(filename, safe="")
        async with httpx.AsyncClient() as c:
            r = await c.delete(f"{API_BASE_URL}/vault/papers/{enc}", headers=get_headers())
            return r.status_code == 200
    except: return False

async def fetch_paper_summary(filename: str) -> str:
    try:
        enc = urllib.parse.quote(filename, safe="")
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{API_BASE_URL}/vault/papers/{enc}/summary", headers=get_headers())
            return r.json().get("summary", "") if r.status_code == 200 else ""
    except: return ""

async def fetch_paper_details(filename: str) -> dict:
    try:
        enc = urllib.parse.quote(filename, safe="")
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{API_BASE_URL}/vault/papers/{enc}/details", headers=get_headers())
            return r.json() if r.status_code == 200 else {}
    except: return {}

async def fetch_paper_references(filename: str) -> list:
    try:
        enc = urllib.parse.quote(filename, safe="")
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{API_BASE_URL}/vault/papers/{enc}/references", headers=get_headers())
            return r.json().get("references", []) if r.status_code == 200 else []
    except: return []

async def reextract_references(filename: str) -> dict:
    try:
        enc = urllib.parse.quote(filename, safe="")
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(f"{API_BASE_URL}/vault/papers/{enc}/reextract-references", headers=get_headers())
            return r.json() if r.status_code == 200 else {}
    except: return {}

async def ingest_arxiv_paper(arxiv_id: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=300.0) as c:
            r = await c.post(f"{API_BASE_URL}/ingest/arxiv", json={"arxiv_id": arxiv_id}, headers=get_headers())
            data = r.json()
            return data.get("message", "Done") if r.status_code == 200 else data.get("detail", "Failed")
    except Exception as e:
        return f"Error: {e}"

async def fetch_bibtex_export() -> str:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{API_BASE_URL}/vault/export/bibtex", headers=get_headers())
            return r.text if r.status_code == 200 else ""
    except: return ""

async def search_passages(query: str, limit: int = 10) -> list:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{API_BASE_URL}/vault/search/passages",
                             json={"query": query, "limit": limit}, headers=get_headers())
            return r.json().get("passages", []) if r.status_code == 200 else []
    except: return []

async def fetch_knowledge_graph() -> dict:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{API_BASE_URL}/vault/graph", headers=get_headers())
            return r.json() if r.status_code == 200 else {}
    except: return {}

# --- Chat ---
async def fetch_threads():
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{API_BASE_URL}/chat/threads", headers=get_headers())
            return r.json().get("threads", [])
    except: return []

async def fetch_chat_history(thread_id):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{API_BASE_URL}/chat/history/{thread_id}", headers=get_headers())
            return r.json().get("messages", [])
    except: return []

async def stream_query_backend(query, filters=None):
    payload = {
        "query": query, "filters": filters,
        "thread_id": st.session_state.thread_id,
        "tenant_id": st.session_state.user_info.get("tenant_id"),
    }
    sse_timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=sse_timeout) as client:
        async with aconnect_sse(client, "POST", f"{API_BASE_URL}/query",
                                json=payload, headers=get_headers()) as es:
            async for ev in es.aiter_sse():
                if ev.data == "[DONE]": break
                yield json.loads(ev.data)

async def stream_lit_review(filenames: list, question: str):
    payload = {"filenames": filenames, "question": question}
    sse_timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=sse_timeout) as client:
        async with aconnect_sse(client, "POST", f"{API_BASE_URL}/vault/analyze/literature-review",
                                json=payload, headers=get_headers()) as es:
            async for ev in es.aiter_sse():
                if ev.data == "[DONE]": break
                yield json.loads(ev.data)

# --- Stats ---
async def fetch_usage_stats():
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{API_BASE_URL}/stats/usage", headers=get_headers())
            return r.json() if r.status_code == 200 else {}
    except: return {}

# --- Notes ---
async def fetch_notes():
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{API_BASE_URL}/notes", headers=get_headers())
            return r.json().get("notes", []) if r.status_code == 200 else []
    except: return []

async def create_note(title: str, content: str) -> dict:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{API_BASE_URL}/notes",
                             json={"title": title, "content": content}, headers=get_headers())
            return r.json() if r.status_code == 201 else {}
    except: return {}

async def update_note(note_id: str, title: str, content: str) -> dict:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.put(f"{API_BASE_URL}/notes/{note_id}",
                            json={"title": title, "content": content}, headers=get_headers())
            return r.json() if r.status_code == 200 else {}
    except: return {}

async def delete_note(note_id: str) -> bool:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.delete(f"{API_BASE_URL}/notes/{note_id}", headers=get_headers())
            return r.status_code == 200
    except: return False

# --- Reading Queue ---
async def fetch_queue() -> list:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{API_BASE_URL}/queue", headers=get_headers())
            return r.json().get("items", []) if r.status_code == 200 else []
    except: return []

async def add_to_queue(title: str, arxiv_id: str = "", url: str = "") -> bool:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{API_BASE_URL}/queue",
                             json={"title": title, "arxiv_id": arxiv_id or None, "url": url or None},
                             headers=get_headers())
            return r.status_code == 201
    except: return False

async def update_queue_status(item_id: str, status: str) -> bool:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.patch(f"{API_BASE_URL}/queue/{item_id}",
                              json={"status": status}, headers=get_headers())
            return r.status_code == 200
    except: return False

async def delete_queue_item(item_id: str) -> bool:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.delete(f"{API_BASE_URL}/queue/{item_id}", headers=get_headers())
            return r.status_code == 200
    except: return False

# --- Arxiv Monitors ---
async def fetch_monitors() -> list:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{API_BASE_URL}/monitors", headers=get_headers())
            return r.json().get("monitors", []) if r.status_code == 200 else []
    except: return []

async def create_monitor(keywords: str) -> bool:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{API_BASE_URL}/monitors",
                             json={"keywords": keywords}, headers=get_headers())
            return r.status_code == 201
    except: return False

async def delete_monitor(monitor_id: str) -> bool:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.delete(f"{API_BASE_URL}/monitors/{monitor_id}", headers=get_headers())
            return r.status_code == 200
    except: return False

async def fetch_alerts() -> list:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{API_BASE_URL}/monitors/alerts", headers=get_headers())
            return r.json().get("alerts", []) if r.status_code == 200 else []
    except: return []

async def ingest_file_with_progress(file):
    """Upload a single PDF, stream worker progress, return result dict or None."""
    try:
        upload_timeout = httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0)
        sse_timeout    = httpx.Timeout(connect=10.0, read=None,  write=30.0,  pool=10.0)

        async with httpx.AsyncClient(timeout=upload_timeout) as upload_client:
            files = {"file": (file.name, file.getvalue(), "application/pdf")}
            progress_bar = st.progress(0, text=f"Uploading {file.name}...")
            r = await upload_client.post(f"{API_BASE_URL}/ingest", files=files, headers=get_headers())
            if r.status_code != 200:
                st.error(f"Failed to enqueue {file.name}: {r.text}")
                progress_bar.empty()
                return None
            job_id = r.json().get("job_id")

        async with httpx.AsyncClient(timeout=sse_timeout) as sse_client:
            async with aconnect_sse(sse_client, "GET", f"{API_BASE_URL}/ingest/stream/{job_id}",
                                    headers=get_headers()) as es:
                async for ev in es.aiter_sse():
                    data = json.loads(ev.data)
                    if data.get("type") == "progress":
                        progress_bar.progress(data["value"] / 100, text=data["message"])
                    elif data.get("type") == "completed":
                        progress_bar.empty()
                        return data["result"]
                    elif data.get("type") == "error":
                        st.error(data["message"])
                        progress_bar.empty()
                        return None
    except Exception as e:
        st.error(f"Ingestion failed for {file.name}: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH UI
# ═══════════════════════════════════════════════════════════════════════════════

if not st.session_state.auth_token:
    st.markdown("<h1 style='text-align: center; color: #6366f1;'>🧬 ResearchHub</h1>", unsafe_allow_html=True)
    _, col, _ = st.columns([1, 2, 1])
    with col:
        tab_login, tab_reg = st.tabs(["Login", "Register"])
        with tab_login:
            with st.form("login_form"):
                user = st.text_input("Username")
                pw   = st.text_input("Password", type="password")
                if st.form_submit_button("Enter Workspace"):
                    res, code = run_async(login_user(user, pw))
                    if code == 200:
                        st.session_state.auth_token = res["access_token"]
                        st.session_state.user_info  = res
                        st.rerun()
                    else:
                        st.error(_error_msg(res, "Login failed"))
        with tab_reg:
            with st.form("reg_form"):
                new_user = st.text_input("New Username")
                new_pw   = st.text_input("New Password", type="password")
                team     = st.text_input("Team Code")
                if st.form_submit_button("Register"):
                    res, code = run_async(register_user(new_user, new_pw, team))
                    if code == 200:
                        st.success("Registered! Please login.")
                    else:
                        st.error(_error_msg(res, "Registration failed"))
    st.stop()


# ═══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════

vault_papers = run_async(fetch_vault_papers())

with st.sidebar:
    st.title("🧬 ResearchHub")
    st.write(f"👤 {st.session_state.user_info.get('username', '')}")

    col_new, col_out = st.columns(2)
    with col_new:
        if st.button("➕ New Chat", width='stretch'):
            st.session_state.messages   = []
            st.session_state.thread_id  = None
            st.session_state.filter_paper = None
            st.rerun()
    with col_out:
        if st.button("Logout", width='stretch'):
            st.session_state.auth_token = None
            st.rerun()

    st.divider()

    # ── Upload ───────────────────────────────────────────────────────────────
    uploaded_files = st.file_uploader(
        "PDF", type="pdf", label_visibility="collapsed",
        accept_multiple_files=True,
        help="Select one or more PDFs to index into your vault."
    )

    if uploaded_files:
        existing_filenames = {p["filename"] for p in vault_papers}
        new_files     = [f for f in uploaded_files if f.name not in existing_filenames]
        dup_files     = [f for f in uploaded_files if f.name in existing_filenames]

        if dup_files:
            st.warning(f"Already in vault: {', '.join(f.name for f in dup_files)}")
            st.caption("Check the box below to re-ingest and extract missing references.")
            if st.checkbox("Replace & re-index", key="confirm_replace"):
                new_files = uploaded_files  # include duplicates for re-ingestion

        btn_label = (
            f"Re-index {len(new_files)} Paper{'s' if len(new_files) > 1 else ''}"
            if all(f.name in existing_filenames for f in new_files)
            else f"Index {len(new_files)} Paper{'s' if len(new_files) > 1 else ''}"
        )
        if new_files and st.button(btn_label, width='stretch'):
            ingested = 0
            for f in new_files:
                res = run_async(ingest_file_with_progress(f))
                if res:
                    ingested += 1
            if ingested:
                st.success(f"Indexed {ingested} paper(s).")
                st.rerun()

    st.divider()

    # ── Vault (compact — full actions live in the Library tab) ────────────────
    st.caption(f"📚 {len(vault_papers)} paper{'s' if len(vault_papers) != 1 else ''} in vault" if vault_papers else "📚 No papers yet")
    for paper in vault_papers:
        display_title = paper.get("title") or paper["filename"]
        if display_title == "Unknown Research Paper":
            display_title = paper["filename"]
        label = display_title if len(display_title) <= 26 else display_title[:24] + "…"
        fn = paper["filename"]
        is_selected = st.session_state.filter_paper == fn
        pc, pd = st.columns([6, 1])
        with pc:
            if st.button(
                label, key=f"focus_{fn}", use_container_width=True,
                help="Chat about this paper",
                type="primary" if is_selected else "secondary",
            ):
                st.session_state.filter_paper = fn
                st.session_state.messages   = []
                st.session_state.thread_id  = None
                st.rerun()
        with pd:
            if st.button("🗑", key=f"del_{fn}", help="Remove from vault"):
                if run_async(delete_vault_paper(fn)): st.rerun()

    if vault_papers:
        scope_options = ["All Papers"] + [p["filename"] for p in vault_papers]
        current_scope = st.session_state.filter_paper or "All Papers"
        if current_scope not in scope_options:
            current_scope = "All Papers"
        chosen = st.selectbox(
            "Chat scope", scope_options,
            index=scope_options.index(current_scope),
            label_visibility="collapsed",
            help="Scope chat questions to a single paper",
        )
        st.session_state.filter_paper = None if chosen == "All Papers" else chosen

    st.divider()

    # ── Reading Queue ─────────────────────────────────────────────────────────
    with st.expander("📋 Queue", expanded=False):
        queue_items = run_async(fetch_queue())
        pending = [i for i in queue_items if i["status"] != "done"]
        for item in pending:
            icon = {"queued": "🔖", "reading": "📖"}.get(item["status"], "•")
            ca, cb, cc = st.columns([6, 1, 1])
            with ca:
                st.caption(f"{icon} {item['title'][:35]}")
            with cb:
                next_s = {"queued": "reading", "reading": "done"}.get(item["status"])
                if next_s and st.button("→", key=f"q_adv_{item['id']}", help="Advance status"):
                    run_async(update_queue_status(item["id"], next_s)); st.rerun()
            with cc:
                if st.button("✕", key=f"q_del_{item['id']}", help="Remove"):
                    run_async(delete_queue_item(item["id"])); st.rerun()
        if not pending:
            st.caption("Empty")

        with st.form("add_queue_form"):
            q_title    = st.text_input("Title", placeholder="Paper title…", label_visibility="collapsed")
            q_arxiv_id = st.text_input("arXiv ID", placeholder="2305.10601 (optional)", label_visibility="collapsed")
            if st.form_submit_button("+ Add", width='stretch'):
                if q_title: run_async(add_to_queue(q_title, q_arxiv_id)); st.rerun()

    # ── Arxiv Alerts ──────────────────────────────────────────────────────────
    alerts  = run_async(fetch_alerts())
    monitors = run_async(fetch_monitors())
    alert_label = f"🔔 Alerts ({len(alerts)})" if alerts else "🔔 Alerts"
    with st.expander(alert_label, expanded=bool(alerts)):
        for alert in alerts[:8]:
            title_short = alert["title"][:55] + ("…" if len(alert["title"]) > 55 else "")
            st.caption(f"**{title_short}** `{alert['arxiv_id']}`")

        if not alerts:
            st.caption("No new alerts." if monitors else "No monitors set up.")

        if monitors:
            st.caption("**Watching:**")
        for m in monitors:
            ma, mb = st.columns([5, 1])
            with ma: st.caption(m["keywords"])
            with mb:
                if st.button("✕", key=f"mon_del_{m['id']}", help="Remove watch"):
                    run_async(delete_monitor(m["id"])); st.rerun()

        with st.form("add_monitor_form"):
            kw = st.text_input("Keywords", placeholder="e.g. RAG 2024", label_visibility="collapsed")
            if st.form_submit_button("+ Watch", width='stretch'):
                if kw: run_async(create_monitor(kw)); st.rerun()

    st.divider()

    # ── Previous Chats ────────────────────────────────────────────────────────
    st.subheader("💬 Previous Chats")
    threads = run_async(fetch_threads())
    if not threads:
        st.caption("No previous chats.")
    else:
        selected_thread = st.selectbox(
            "Select chat", ["Current Chat"] + threads, label_visibility="collapsed"
        )
        if selected_thread != "Current Chat" and selected_thread != st.session_state.thread_id:
            history = run_async(fetch_chat_history(selected_thread))
            st.session_state.messages  = history
            st.session_state.thread_id = selected_thread
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN TABS
# ═══════════════════════════════════════════════════════════════════════════════

tab_chat, tab_library, tab_analyze, tab_notes, tab_workspace = st.tabs([
    "💬 Research Chat", "📚 Library", "🔭 Analyze", "📝 My Notes", "📊 Workspace"
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — RESEARCH CHAT
# ═══════════════════════════════════════════════════════════════════════════════
with tab_chat:
    header_col, export_col = st.columns([5, 1])
    with header_col:
        st.title("Research Chat")
    with export_col:
        if st.session_state.messages:
            lines = []
            for m in st.session_state.messages:
                prefix = "**You:**" if m["role"] == "user" else "**Assistant:**"
                lines.append(f"{prefix}\n{m['content']}\n")
            st.download_button(
                "⬇ Export",
                data="\n---\n".join(lines),
                file_name="research_session.md",
                mime="text/markdown",
                use_container_width=True,
            )

    # ── Controls row ──────────────────────────────────────────────────────────
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([3, 1, 1])
    with ctrl_col1:
        paper_options = ["All Papers"] + [p["filename"] for p in vault_papers]
        filter_idx = 0
        if st.session_state.filter_paper and st.session_state.filter_paper in paper_options:
            filter_idx = paper_options.index(st.session_state.filter_paper)
        selected_paper = st.selectbox(
            "Scope", paper_options, index=filter_idx,
            label_visibility="collapsed", key="paper_filter_select"
        )
        st.session_state.filter_paper = None if selected_paper == "All Papers" else selected_paper
    with ctrl_col2:
        st.session_state.highlight_mode = st.toggle(
            "💡 Quote mode",
            value=st.session_state.highlight_mode,
            help="Asks the AI to quote exact passages from the paper"
        )
    with ctrl_col3:
        st.session_state.passage_mode = st.toggle(
            "🔎 Passages",
            value=st.session_state.passage_mode,
            help="Search for raw text chunks — no LLM, just retrieval"
        )

    # ── Passage search mode ───────────────────────────────────────────────────
    if st.session_state.passage_mode:
        st.info("**Passage Search Mode** — queries return raw text chunks directly from your vault, no AI generation.")
        with st.form("passage_form"):
            p_query = st.text_input("Search passages", placeholder="e.g. attention mechanism dropout")
            if st.form_submit_button("Search"):
                with st.spinner("Searching vault..."):
                    st.session_state.passage_results = run_async(search_passages(p_query, limit=10))

        if st.session_state.passage_results:
            st.markdown(f"**{len(st.session_state.passage_results)} passages found:**")
            for i, p in enumerate(st.session_state.passage_results):
                with st.expander(f"[{i+1}] {p.get('filename', '')} — score {p.get('score', 0):.3f}"):
                    st.write(p.get("text", ""))
    else:
        # ── Starter chips ─────────────────────────────────────────────────────
        STARTER_CHIPS = [
            "Summarise this paper in plain language",
            "What methodology does this paper use?",
            "What are the key findings and contributions?",
            "What are the limitations of this research?",
        ]
        if vault_papers and not st.session_state.messages:
            st.markdown("##### Suggested questions")
            chip_cols = st.columns(4)
            for i, chip in enumerate(STARTER_CHIPS):
                with chip_cols[i]:
                    st.markdown('<div class="chip-btn">', unsafe_allow_html=True)
                    if st.button(chip, key=f"chip_{i}", width='stretch'):
                        st.session_state.chip_query = chip
                    st.markdown("</div>", unsafe_allow_html=True)
            st.divider()

        # ── Chat history ──────────────────────────────────────────────────────
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("metrics"):
                    m = msg["metrics"]
                    st.caption(f"⚡ {m['latency_ms']}ms | 🪙 {m['tokens_in'] + m['tokens_out']} tokens")

        # ── Input ─────────────────────────────────────────────────────────────
        prompt = st.chat_input("Query your research library...")

        if st.session_state.chip_query and not prompt:
            prompt = st.session_state.chip_query
            st.session_state.chip_query = None

        if prompt:
            effective_query = prompt
            if st.session_state.highlight_mode:
                effective_query = (
                    "Find and quote exact passages from the paper that answer this question, "
                    f"then summarise: {prompt}"
                )

            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                status_box   = st.empty()
                response_box = st.empty()
                metrics_box  = st.empty()

                filters = {"year_min": 1990, "year_max": 2026}
                if st.session_state.filter_paper:
                    filters["filename"] = st.session_state.filter_paper

                async def handle_stream():
                    full_text = ""
                    metrics   = None
                    try:
                        async for event in stream_query_backend(effective_query, filters):
                            if event.get("status") == "agent_started":
                                # Persist thread_id so subsequent messages continue
                                # the same LangGraph conversation checkpoint.
                                st.session_state.thread_id = event.get("thread_id")
                                status_box.info("Thinking...")
                            elif event.get("type") == "token":
                                full_text += event.get("content", "")
                                # Streaming cursor gives the user visual feedback
                                # that text is arriving incrementally.
                                response_box.markdown(full_text + "▌")
                            elif event.get("type") == "tool_start":
                                status_box.warning(f"Using {event.get('tool')}...")
                            elif event.get("type") == "tool_end":
                                status_box.success(f"Finished {event.get('tool')}")
                            elif event.get("type") == "metrics":
                                metrics = event
                                metrics_box.caption(
                                    f"⚡ {event['latency_ms']}ms | 🪙 {event['tokens_in'] + event['tokens_out']} tokens"
                                )
                    except Exception as e:
                        full_text = f"Connection error: {e}. Please try again."
                    finally:
                        response_box.markdown(full_text)
                        status_box.empty()
                        if full_text:
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": full_text,
                                "metrics": metrics,
                            })

                run_async(handle_stream())
                st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — LIBRARY
# ═══════════════════════════════════════════════════════════════════════════════
with tab_library:
    st.title("Library")

    if not vault_papers:
        st.info("No papers in your vault yet. Upload PDFs using the sidebar.")
    else:
        bib_content_lib = run_async(fetch_bibtex_export())
        if bib_content_lib:
            st.download_button(
                "⬇ Export All as BibTeX",
                data=bib_content_lib,
                file_name="vault.bib",
                mime="text/plain",
            )

        for paper in vault_papers:
            display_title = paper.get("title") or paper["filename"]
            if display_title == "Unknown Research Paper":
                display_title = paper["filename"]
            fn = paper["filename"]

            meta_parts = []
            if paper.get("authors"): meta_parts.append(paper["authors"][0].split(",")[0])
            if paper.get("year"):    meta_parts.append(str(paper["year"]))
            expander_label = display_title + (f"  ·  {' · '.join(meta_parts)}" if meta_parts else "")

            with st.expander(expander_label, expanded=False):
                # Metadata row
                if paper.get("keywords"):
                    st.caption("**Keywords:** " + ", ".join(paper["keywords"][:6]))
                if paper.get("journal"):
                    st.caption(f"**Journal:** {paper['journal']}")

                r1a, r1b, r1c, r1d, r1e = st.columns(5)
                with r1a:
                    if st.button("💬 Chat", key=f"lib_chat_{fn}", use_container_width=True,
                                 help="Scope chat to this paper"):
                        st.session_state.filter_paper = fn
                        st.session_state.messages     = []
                        st.session_state.thread_id    = None
                        st.rerun()
                with r1b:
                    if st.button("📋 Cite", key=f"lib_cite_{fn}", use_container_width=True):
                        st.session_state[f"_lib_cite_{fn}"] = format_apa_citation(paper)
                with r1c:
                    if st.button("📝 Summary", key=f"lib_sum_{fn}", use_container_width=True):
                        with st.spinner("Generating summary…"):
                            st.session_state[f"_lib_sum_{fn}"] = run_async(fetch_paper_summary(fn)) or "Summary not available."
                with r1d:
                    if st.button("🔬 Details", key=f"lib_det_{fn}", use_container_width=True):
                        try:
                            st.session_state[f"_lib_det_{fn}"] = run_async(fetch_paper_details(fn)) or {}
                        except Exception:
                            st.session_state[f"_lib_det_{fn}"] = {}
                with r1e:
                    if st.button("🗑 Delete", key=f"lib_del_{fn}", use_container_width=True):
                        if run_async(delete_vault_paper(fn)): st.rerun()

                if f"_lib_cite_{fn}" in st.session_state:
                    st.code(st.session_state[f"_lib_cite_{fn}"], language=None)

                if f"_lib_sum_{fn}" in st.session_state:
                    st.info(st.session_state[f"_lib_sum_{fn}"])

                if f"_lib_det_{fn}" in st.session_state:
                    d = st.session_state[f"_lib_det_{fn}"] or {}
                    shown = 0
                    for field, label_text in [("contribution","Contribution"),("dataset","Dataset"),("limitations","Limitations")]:
                        if d.get(field):
                            st.markdown(f"**{label_text}:** {d[field]}")
                            shown += 1
                    if d.get("baselines"):
                        st.markdown("**Baselines:** " + ", ".join(d["baselines"]))
                        shown += 1
                    if shown == 0:
                        st.caption("Details not populated — re-ingest this paper to generate them.")

                # References section
                st.divider()
                ref_col, _ = st.columns([2, 3])
                with ref_col:
                    if st.button("📖 Load References", key=f"lib_refs_{fn}", use_container_width=True):
                        try:
                            st.session_state[f"_lib_refs_{fn}"] = run_async(fetch_paper_references(fn)) or []
                        except Exception:
                            st.session_state[f"_lib_refs_{fn}"] = []

                if f"_lib_refs_{fn}" in st.session_state:
                    refs = st.session_state[f"_lib_refs_{fn}"] or []
                    if refs:
                        for i, ref in enumerate(refs[:15]):
                            ref_arxiv = ref.get("arxiv_id")
                            year  = ref.get("year") or ""
                            title = (ref.get("title") or "Untitled")[:70]
                            rlabel = f"· {title} ({year})" if year else f"· {title}"
                            if ref_arxiv:
                                rc_text, rc_btn = st.columns([6, 1])
                                with rc_text:
                                    st.caption(f"{rlabel} `{ref_arxiv}`")
                                with rc_btn:
                                    if st.button("⬇", key=f"lib_aingest_{fn}_{i}", help=f"Ingest {ref_arxiv}"):
                                        with st.spinner(f"Ingesting {ref_arxiv}…"):
                                            msg = run_async(ingest_arxiv_paper(ref_arxiv))
                                        st.toast(msg)
                            else:
                                st.caption(rlabel)
                        if len(refs) > 15: st.caption(f"…+{len(refs)-15} more")
                    else:
                        st.caption("No references extracted.")
                        if st.button("Extract References", key=f"lib_reextract_{fn}", use_container_width=True):
                            with st.spinner("Extracting references…"):
                                result = run_async(reextract_references(fn))
                            count = result.get("references_found", 0)
                            if count:
                                st.session_state[f"_lib_refs_{fn}"] = run_async(fetch_paper_references(fn))
                                st.rerun()
                            else:
                                st.warning("No references found in stored text.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — ANALYZE (Literature Review + Knowledge Graph)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_analyze:
    st.title("Analyze")
    if len(vault_papers) < 2:
        st.info("Upload at least 2 papers to unlock analysis features.")
    else:
        sub_lit, sub_graph, sub_compare = st.tabs(["📝 Literature Review", "🕸 Knowledge Graph", "🔄 Compare Two Papers"])

        # ── Literature Review ─────────────────────────────────────────────────
        with sub_lit:
            st.subheader("Literature Review Generator")
            st.write("Select papers and a research question — the AI synthesises a structured review across all of them.")

            paper_filenames = [p["filename"] for p in vault_papers]
            selected_for_review = st.multiselect(
                "Papers to include (up to 5)", paper_filenames,
                max_selections=5, key="lit_review_papers"
            )
            review_question = st.text_area(
                "Research question",
                value="What are the key contributions, methodologies, and open problems across these papers?",
                height=80, key="lit_review_q"
            )

            if st.button("Generate Review", type="primary", width='stretch',
                         disabled=not selected_for_review):
                result_box = st.empty()
                buf = {"text": ""}

                async def handle_lit_review():
                    async for event in stream_lit_review(selected_for_review, review_question):
                        if event.get("type") == "token":
                            buf["text"] += event.get("content", "")
                            result_box.markdown(buf["text"] + "▌")
                        elif event.get("type") == "error":
                            result_box.error(event.get("content", "Review generation failed."))
                            return
                    result_box.markdown(buf["text"])
                    st.session_state.lit_review_result = buf["text"]

                run_async(handle_lit_review())

            elif st.session_state.lit_review_result:
                st.markdown(st.session_state.lit_review_result)

                # Export lit review as markdown
                st.download_button(
                    "⬇ Download Review",
                    data=st.session_state.lit_review_result,
                    file_name="literature_review.md",
                    mime="text/markdown",
                )

        # ── Knowledge Graph ───────────────────────────────────────────────────
        with sub_graph:
            st.subheader("Paper Relationship Graph")
            st.write("Papers connected by shared authors or keywords. Useful for spotting research clusters.")

            if st.button("Build Graph", width='stretch'):
                with st.spinner("Computing edges..."):
                    st.session_state.graph_data = run_async(fetch_knowledge_graph())

            if st.session_state.graph_data:
                graph = st.session_state.graph_data
                nodes = graph.get("nodes", [])
                edges = graph.get("edges", [])

                st.metric("Papers (nodes)", len(nodes))
                st.metric("Connections (edges)", len(edges))

                if edges:
                    st.markdown("**Strongest connections:**")
                    # Sort by weight descending, show top 15
                    sorted_edges = sorted(edges, key=lambda e: e.get("weight", 0), reverse=True)
                    for edge in sorted_edges[:15]:
                        reason = edge.get("reason", "")
                        st.write(
                            f"• **{edge['source']}** ↔ **{edge['target']}**"
                            + (f" ({reason})" if reason else "")
                        )
                else:
                    st.info("No shared authors or keywords found between these papers. "
                            "Try uploading papers from the same research group.")

        # ── Compare Two Papers ────────────────────────────────────────────────
        with sub_compare:
            st.subheader("Head-to-Head Comparison")
            col_a, col_b = st.columns(2)
            with col_a:
                paper_a = st.selectbox("Paper A", paper_filenames, key="cmp_a")
            with col_b:
                available_b = [f for f in paper_filenames if f != paper_a]
                paper_b = st.selectbox("Paper B", available_b, key="cmp_b")

            question = st.text_input(
                "Research question",
                value="What are the key similarities and differences?",
                key="cmp_q"
            )

            if st.button("Compare", width='stretch', type="primary"):
                # Use the literature review endpoint with two papers
                result_box_cmp = st.empty()
                buf_cmp = {"text": ""}

                async def handle_compare():
                    async for event in stream_lit_review([paper_a, paper_b], question):
                        if event.get("type") == "token":
                            buf_cmp["text"] += event.get("content", "")
                            result_box_cmp.markdown(buf_cmp["text"] + "▌")
                        elif event.get("type") == "error":
                            result_box_cmp.error(event.get("content", "Compare failed."))
                            return
                    result_box_cmp.markdown(buf_cmp["text"])

                run_async(handle_compare())


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — MY NOTES
# ═══════════════════════════════════════════════════════════════════════════════
with tab_notes:
    st.title("My Notes")

    if st.session_state.notes_cache is None:
        st.session_state.notes_cache = run_async(fetch_notes())

    notes = st.session_state.notes_cache or []

    list_col, editor_col = st.columns([1, 3])

    with list_col:
        if st.button("＋ New Note", width='stretch', type="primary"):
            st.session_state.note_selected_id = None
            st.session_state["ntitle_new"]    = "Untitled Note"
            st.session_state["ncontent_new"]  = ""
            st.rerun()
        if st.button("↻ Refresh", width='stretch'):
            st.session_state.notes_cache = run_async(fetch_notes())
            st.rerun()
        st.divider()

        if not notes:
            st.caption("No notes yet.")
        else:
            for note in notes:
                label = note["title"] if len(note["title"]) <= 26 else note["title"][:24] + "…"
                is_selected = note["id"] == st.session_state.note_selected_id
                if st.button(
                    ("▶ " if is_selected else "") + label,
                    key=f"note_sel_{note['id']}",
                    width='stretch',
                    type="primary" if is_selected else "secondary",
                ):
                    st.session_state.note_selected_id       = note["id"]
                    st.session_state[f"ntitle_{note['id']}"]   = note["title"]
                    st.session_state[f"ncontent_{note['id']}"] = note["content"]
                    st.rerun()

    with editor_col:
        nk     = st.session_state.note_selected_id or "new"
        is_new = st.session_state.note_selected_id is None

        if nk == "new" and "ntitle_new" not in st.session_state:
            st.info("Select a note from the list or click **＋ New Note**.")
        else:
            title_key   = f"ntitle_{nk}"
            content_key = f"ncontent_{nk}"

            st.text_input("Title", key=title_key, label_visibility="collapsed", placeholder="Note title…")

            edit_sub, preview_sub = st.tabs(["✏️ Edit", "👁 Preview"])
            with edit_sub:
                st.text_area("Content", key=content_key, height=420,
                             label_visibility="collapsed",
                             placeholder="Write your note here using **Markdown**…")
            with preview_sub:
                md_content = st.session_state.get(content_key, "")
                if md_content.strip(): st.markdown(md_content)
                else: st.caption("Nothing to preview yet.")

            save_col, del_col = st.columns([3, 1])
            with save_col:
                if st.button("💾 Save Note", width='stretch', type="primary", key="note_save"):
                    title   = st.session_state.get(title_key) or "Untitled Note"
                    content = st.session_state.get(content_key, "")
                    if is_new:
                        result = run_async(create_note(title, content))
                        if result:
                            new_id = result["id"]
                            st.session_state.note_selected_id          = new_id
                            st.session_state[f"ntitle_{new_id}"]       = title
                            st.session_state[f"ncontent_{new_id}"]     = content
                            st.session_state.pop("ntitle_new", None)
                            st.session_state.pop("ncontent_new", None)
                            st.success("Note created.")
                        else:
                            st.error("Failed to save.")
                    else:
                        result = run_async(update_note(st.session_state.note_selected_id, title, content))
                        st.success("Saved.") if result else st.error("Failed to save.")
                    st.session_state.notes_cache = run_async(fetch_notes())
                    st.rerun()

            with del_col:
                if not is_new and st.button("🗑 Delete", width='stretch', key="note_del"):
                    ok = run_async(delete_note(st.session_state.note_selected_id))
                    if ok:
                        st.session_state.pop(title_key, None)
                        st.session_state.pop(content_key, None)
                        st.session_state.note_selected_id = None
                        st.session_state.notes_cache      = run_async(fetch_notes())
                        st.rerun()
                    else:
                        st.error("Delete failed.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — WORKSPACE (Usage + Activity Timeline)
# ═══════════════════════════════════════════════════════════════════════════════
with tab_workspace:
    st.title("Workspace")

    stats = run_async(fetch_usage_stats())
    if not stats:
        st.info("No usage data available yet.")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Papers in Vault",  len(vault_papers))
        m2.metric("Total Tokens",     f"{stats.get('total_tokens', 0):,}")
        m3.metric("Total Cost (USD)", f"${stats.get('total_cost_usd', 0):.4f}")
        m4.metric("Avg Faithfulness", f"{stats.get('avg_faithfulness', 1.0):.0%}")

        history = stats.get("history", [])
        if history:
            st.subheader("Activity Timeline")
            # Newest first
            for entry in reversed(history):
                ts = entry.get("time", "")
                try:   ts = datetime.fromisoformat(ts).strftime("%b %d, %H:%M")
                except: pass
                cost_str   = f"${entry.get('cost', 0):.5f}"
                event_type = entry.get("type", "event").capitalize()
                icon       = {"Ingest": "📥", "Query": "🔍"}.get(event_type, "•")
                st.write(f"{icon} **{ts}** — {event_type} — {cost_str}")

        # Vault summary table
        if vault_papers:
            st.subheader("Vault Overview")
            rows = []
            for p in vault_papers:
                authors = ", ".join((p.get("authors") or [])[:2])
                if len(p.get("authors") or []) > 2: authors += " et al."
                rows.append({
                    "Title": (p.get("title") or p["filename"])[:60],
                    "Authors": authors,
                    "Year": p.get("year") or "",
                    "Keywords": ", ".join((p.get("keywords") or [])[:3]),
                })
            st.dataframe(rows, use_container_width=True)
