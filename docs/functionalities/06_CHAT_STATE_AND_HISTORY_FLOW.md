# 06 Chat State And History Flow

## Why this functionality exists

The assistant should feel conversational, not like a sequence of unrelated prompts.

That requires:

- a stable `thread_id`
- persisted graph state
- a way to reconstruct prior messages in the UI

## Design thinking in order

### Step 1: Give each conversation a stable identifier

Without a thread ID, every query is stateless.

### Step 2: Persist graph state outside the request lifecycle

LangGraph state must survive beyond one HTTP request.

### Step 3: Make history queryable by the frontend

The UI must be able to list threads and rehydrate the selected conversation.

## Dependencies introduced

- `langgraph`
- `langgraph.checkpoint.sqlite.aio`

## Files involved

- `main.py`
- `app/core/graph.py`
- `app/api/routes.py`
- `dashboard.py`

## Runtime execution flow

### 1. Thread ID is established

In `handle_query()`:

- `thread_id` comes from the request if present
- otherwise a UUID is generated

### 2. Graph config is built

The config passed into LangGraph contains:

- `configurable.thread_id`

That is the lookup key for checkpoint state.

### 3. Startup attaches checkpointer

`main.py` creates `AsyncSqliteSaver` and compiles the graph with it.

This means every query can load and update state for a specific thread.

### 4. Messages accumulate in graph state

`ResearchState.messages` is append-merged via `operator.add`.

That is how prior model responses and tool outputs remain available in later turns.

### 5. Frontend stores current thread

`dashboard.py` updates:

- `st.session_state.thread_id`

This lets later prompts continue the same conversation.

### 6. Thread listing endpoint reads checkpoints

`app/api/routes.py -> get_threads()`

This iterates checkpoint entries and extracts thread IDs.

### 7. History endpoint formats messages

`app/api/routes.py -> get_chat_history()`

This loads the checkpoint, reads `channel_values.messages`, and converts LangChain message objects into UI-friendly `{role, content}` dictionaries.

## Flow across files

`main.py`
-> `app/core/graph.py`
-> `app/api/routes.py`
-> `dashboard.py`

## What to rebuild first if doing this from scratch

1. Add `thread_id` to query API.
2. Add checkpointer-backed graph compilation.
3. Add history endpoint.
4. Add thread list endpoint.
5. Add frontend thread persistence and selection.
