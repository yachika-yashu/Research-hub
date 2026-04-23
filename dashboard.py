import streamlit as st
import httpx
import os
import pandas as pd
import asyncio
from datetime import datetime
from typing import List, Optional
from httpx_sse import aconnect_sse
import json

# --- PAGE CONFIG ---
st.set_page_config(
    page_title="ResearchHub | Collaborative Research",
    page_icon="🧬",
    layout="wide",
)

# --- CONFIGURATION ---
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")

# --- CUSTOM CSS (Premium UI Overhaul) ---
st.markdown("""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600&display=swap" rel="stylesheet">
    
    <style>
    /* Global Styles */
    html, body, [data-testid="stSidebar"] {
        font-family: 'Outfit', sans-serif !important;
        background-color: #050505 !important;
    }
    
    .main {
        background: radial-gradient(circle at top right, #11111d 0%, #050505 100%);
    }

    /* Sidebar Glassmorphism */
    [data-testid="stSidebar"] {
        background-color: rgba(10, 10, 15, 0.7) !important;
        backdrop-filter: blur(12px);
        border-right: 1px solid rgba(255, 255, 255, 0.05);
    }

    /* Cards & Containers */
    .citation-card {
        background: rgba(255, 255, 255, 0.03);
        border-radius: 12px;
        padding: 18px;
        margin-bottom: 12px;
        border: 1px solid rgba(255, 255, 255, 0.08);
        transition: all 0.3s ease;
    }
    .citation-card:hover {
        background: rgba(255, 255, 255, 0.05);
        border-color: #6366f1;
        transform: translateY(-2px);
        box-shadow: 0 4px 20px rgba(99, 102, 241, 0.15);
    }
    </style>
""", unsafe_allow_html=True)

# --- STATE MANAGEMENT ---
if "auth_token" not in st.session_state:
    st.session_state.auth_token = None
if "user_info" not in st.session_state:
    st.session_state.user_info = {}
if "messages" not in st.session_state:
    st.session_state.messages = []
if "current_citations" not in st.session_state:
    st.session_state.current_citations = []
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None

# --- API HELPERS ---
def get_headers():
    if st.session_state.auth_token:
        return {"Authorization": f"Bearer {st.session_state.auth_token}"}
    return {}

async def register_user(username, password, team_code):
    try:
        async with httpx.AsyncClient() as client:
            payload = {"username": username, "password": password, "team_code": team_code}
            resp = await client.post(f"{API_BASE_URL}/auth/register", json=payload)
            return resp.json(), resp.status_code
    except Exception as e:
        return {"detail": str(e)}, 500

async def login_user(username, password):
    try:
        async with httpx.AsyncClient() as client:
            data = {"username": username, "password": password}
            resp = await client.post(f"{API_BASE_URL}/auth/token", data=data)
            return resp.json(), resp.status_code
    except Exception as e:
        return {"detail": str(e)}, 500

async def fetch_threads():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{API_BASE_URL}/chat/threads", headers=get_headers())
            return resp.json().get("threads", [])
    except: return []

async def fetch_chat_history(thread_id):
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{API_BASE_URL}/chat/history/{thread_id}", headers=get_headers())
            return resp.json().get("messages", [])
    except: return []

async def ingest_file(file):
    """Handles streaming ingestion with progress updates."""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            files = {"file": (file.name, file.getvalue(), "application/pdf")}
            progress_bar = st.progress(0, text="Initializing ingestion...")
            
            async with aconnect_sse(client, "POST", f"{API_BASE_URL}/ingest", files=files, headers=get_headers()) as event_source:
                async for event in event_source.aiter_sse():
                    data = json.loads(event.data)
                    if data.get("type") == "progress":
                        progress_bar.progress(data["value"] / 100, text=data["message"])
                    elif data.get("type") == "completed":
                        progress_bar.empty()
                        return data["result"]
                    elif data.get("type") == "error":
                        st.error(data["message"])
                        return None
    except Exception as e:
        st.error(f"Ingestion failed: {str(e)}")
        return None

async def stream_query_backend(query, filters=None):
    payload = {
        "query": query, 
        "filters": filters, 
        "thread_id": st.session_state.thread_id,
        "tenant_id": st.session_state.user_info.get("tenant_id")
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with aconnect_sse(client, "POST", f"{API_BASE_URL}/query", json=payload, headers=get_headers()) as event_source:
            async for event in event_source.aiter_sse():
                if event.data == "[DONE]":
                    break
                yield json.loads(event.data)

# --- AUTH UI ---
if not st.session_state.auth_token:
    st.markdown("<h1 style='text-align: center; color: #6366f1;'>🧬 ResearchHub</h1>", unsafe_allow_html=True)
    auth_col_1, auth_col_2, auth_col_3 = st.columns([1, 2, 1])
    with auth_col_2:
        tab1, tab2 = st.tabs(["Login", "Register"])
        with tab1:
            with st.form("login_form"):
                user = st.text_input("Username")
                pw = st.text_input("Password", type="password")
                if st.form_submit_button("Enter Workspace"):
                    res, code = asyncio.run(login_user(user, pw))
                    if code == 200:
                        st.session_state.auth_token = res["access_token"]
                        st.session_state.user_info = res
                        st.rerun()
                    else: st.error("Login failed")
        with tab2:
            with st.form("reg_form"):
                new_user = st.text_input("New Username")
                new_pw = st.text_input("New Password", type="password")
                team = st.text_input("Team Code")
                if st.form_submit_button("Register"):
                    res, code = asyncio.run(register_user(new_user, new_pw, team))
                    if code == 200: st.success("Success! Please login.")
                    else: st.error("Failed")
    st.stop()

# --- SIDEBAR ---
with st.sidebar:
    st.title("🧬 ResearchHub")
    st.write(f"👤 {st.session_state.user_info['username']}")
    if st.button("Logout"):
        st.session_state.auth_token = None
        st.rerun()
    
    if st.button("➕ New Chat"):
        st.session_state.messages = []
        st.session_state.thread_id = None
        st.rerun()
        
    st.divider()
    
    # Thread Selector (Step 54)
    threads = asyncio.run(fetch_threads())
    if threads:
        selected_thread = st.selectbox("Previous Chats", ["Current Chat"] + threads)
        if selected_thread != "Current Chat" and selected_thread != st.session_state.thread_id:
            history = asyncio.run(fetch_chat_history(selected_thread))
            st.session_state.messages = history
            st.session_state.thread_id = selected_thread
            st.rerun()
            
    st.divider()
    uploaded_file = st.file_uploader("Ingest PDF", type="pdf")
    if uploaded_file and st.button("Index"):
        res = asyncio.run(ingest_file(uploaded_file))
        if res:
            st.success(f"Indexed: {res['metadata'].get('title', 'Success')}")

# --- MAIN CHAT ---
st.title("Research Control Center")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "metrics" in msg:
            m = msg["metrics"]
            st.caption(f"⚡ {m['latency_ms']}ms | 🪙 {m['tokens_in'] + m['tokens_out']} tokens")

if prompt := st.chat_input("Query your team library..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"): st.markdown(prompt)

    with st.chat_message("assistant"):
        status_box = st.empty()
        response_box = st.empty()
        metrics_box = st.empty()
        
        # We need a function to run the async generator
        async def handle_stream():
            full_text = ""
            filters = {"year_min": 1990, "year_max": 2026}
            metrics = None
            async for event in stream_query_backend(prompt, filters):
                if event.get("status") == "agent_started":
                    st.session_state.thread_id = event.get("thread_id")
                    status_box.info("Thinking...")
                elif event.get("type") == "token":
                    full_text += event.get("content", "")
                    response_box.markdown(full_text + "▌")
                elif event.get("type") == "tool_start":
                    status_box.warning(f"Using {event.get('tool')}...")
                elif event.get("type") == "tool_end":
                    status_box.success(f"Finished {event.get('tool')}")
                elif event.get("type") == "metrics":
                    metrics = event
                    metrics_box.caption(f"⚡ {event['latency_ms']}ms | 🪙 {event['tokens_in'] + event['tokens_out']} tokens")
            
            response_box.markdown(full_text)
            status_box.empty()
            st.session_state.messages.append({
                "role": "assistant", 
                "content": full_text,
                "metrics": metrics
            })

        # Robust async execution for Streamlit
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        loop.run_until_complete(handle_stream())
        st.rerun()
