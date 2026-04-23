# 08 Streamlit And Frontend

## Frontend shape

There is no separate React or HTML SPA. The frontend is a Streamlit app in `dashboard.py` with injected HTML/CSS for styling.

That means the UI execution model is Streamlit's script-rerun model, not a long-lived browser JavaScript state machine.

## Script execution model

Streamlit reruns the Python script on every interaction:

- login submit
- registration submit
- thread selection
- file upload button
- chat submit

Because of that, durable UI state must be stored in `st.session_state`.

Current keys:

- `auth_token`
- `user_info`
- `messages`
- `current_citations`
- `thread_id`

Why this matters:

- local variables disappear on rerun
- session state is the frontend's continuity mechanism
- bugs in session state handling show up as vanishing chat history or lost auth

## Authentication flow

The UI stores the JWT returned from `/auth/token` in `st.session_state.auth_token`. Every later API call uses:

```python
{"Authorization": f"Bearer {token}"}
```

Design implication:

- the frontend is responsible for carrying auth state
- the backend remains stateless with respect to authentication

## API interaction model

The frontend uses `httpx.AsyncClient` for all backend calls.

Patterns:

- ordinary request-response for auth and thread history
- SSE for ingestion and query streaming

Why this split exists:

- auth returns immediately
- ingestion and generation need progress updates

## SSE handling

For ingestion and query, the UI uses `httpx_sse.aconnect_sse`.

During query:

- `agent_started` sets `thread_id`
- `token` appends text incrementally
- `tool_start` and `tool_end` update status box
- `metrics` populates latency and token usage

Why this is user-visible important:

- the user sees progress while the backend is still working
- tooling activity is surfaced, which helps trust and debugging

## Async execution inside Streamlit

Streamlit itself is not an async-first web framework, so the app bridges async calls with:

- `asyncio.run(...)` for one-shot actions
- manual event loop retrieval/creation for streaming chat

Why this is slightly delicate:

- nested event loop misuse can cause runtime errors
- Streamlit reruns can make async coordination awkward

This works, but it is a pragmatic integration rather than a perfectly native async UI stack.

## HTML and CSS role

The HTML in this project is cosmetic:

- imported Google Font
- custom CSS for dark styling and cards

There is no separate HTML frontend architecture. The core frontend behavior is still Streamlit widgets, not DOM-managed application code.

## Production implications

Strengths:

- extremely fast to iterate
- low frontend complexity
- Python-only team can own the full stack

Weaknesses:

- less control over routing and client-side state
- long-lived streaming UX is workable but less flexible than a JS SPA
- scaling frontend sessions is limited by Streamlit's model

## What would change in a rebuild

If keeping Streamlit:

- keep it for internal tooling or operator console
- continue to use SSE to FastAPI

If moving to a richer production frontend:

- replace Streamlit with a dedicated SPA
- keep FastAPI SSE or move to WebSockets
- let the browser manage thread history and stream rendering directly
