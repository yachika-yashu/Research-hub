# 08 Frontend Execution Flow

## Why this functionality exists

The frontend is not a separate SPA. It is a Streamlit operator console for:

- registration and login
- PDF upload
- chat
- thread switching
- live token streaming

To rebuild the product experience, you need to understand how Streamlit execution differs from a React-style frontend.

## Design thinking in order

### Step 1: Optimize for fast product iteration

The team chose Streamlit because it allows a fully usable interface in Python without building a separate frontend stack.

### Step 2: Accept the rerun model

Streamlit reruns the script on interaction. That means UI continuity must live in `st.session_state`.

### Step 3: Use SSE for long operations

Both ingestion and query are long-running enough that progress streaming is required.

## Files involved

- `dashboard.py`
- `app/api/routes.py`
- `app/api/auth.py`

## Runtime execution flow

### 1. Session state is initialized

At app load, `dashboard.py` initializes:

- `auth_token`
- `user_info`
- `messages`
- `current_citations`
- `thread_id`

### 2. Auth path runs

If there is no token:

- login form is shown
- register form is shown

Those forms call:

- `register_user()`
- `login_user()`

which hit the FastAPI auth routes.

### 3. Sidebar management runs

Once authenticated, the sidebar supports:

- logout
- new chat
- previous thread selection
- PDF uploader

Thread selection triggers:

- `fetch_threads()`
- `fetch_chat_history(thread_id)`

### 4. Ingestion path runs

The file uploader plus Index button call:

- `ingest_file()`

This opens an SSE stream and updates the progress bar based on server events.

### 5. Chat path runs

`st.chat_input()` captures the user prompt.

Then:

- user message is added to session state
- `handle_stream()` consumes SSE events from `/query`
- partial text is rendered live
- tool status is rendered live
- metrics are rendered live
- final answer is added to session state

### 6. Rerun occurs

After completion:

- Streamlit reruns
- session state restores continuity

That is how the UI remains coherent despite the rerun model.

## Flow across files

`dashboard.py`
-> `app/api/auth.py`
-> `app/api/routes.py`

## What to rebuild first if doing this from scratch

1. Build login and registration.
2. Build one chat input and streamed answer path.
3. Add file upload and progress bar.
4. Add thread persistence and history view.
5. Add metrics and richer status UI.
