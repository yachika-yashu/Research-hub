# 03 Query And Agent Flow

## Why this functionality exists

The user does not just need retrieval. The user needs an assistant that can:

- decide whether retrieval is needed
- decide whether arXiv search is needed
- decide whether a paper should be auto-ingested
- stream a readable answer back to the UI

That is why the query path is built as an agent flow instead of a single prompt call.

## Design thinking in order

### Step 1: Accept that the assistant needs tools

A pure chat model cannot:

- inspect the local vault
- search arXiv
- run code
- ingest a found paper

So tools had to be defined first.

### Step 2: Accept that tool use is iterative

A single linear chain is often not enough.

The model may need to:

1. look up available papers
2. pick the right file
3. retrieve supporting chunks
4. answer

That drove the decision to use LangGraph with a looped state machine.

### Step 3: Preserve conversation continuity

The query flow should remember prior turns, so a checkpointed graph was added.

### Step 4: Stream the result

The frontend should see token-by-token output and tool status events. That led to SSE translation from graph events into UI events.

## Dependencies introduced

- `fastapi`
- `langgraph`
- `langchain-openai`
- `langchain-core`
- `langchain-community`
- `langchain-experimental`
- `httpx-sse`
- `arxiv`

## Files involved

- `app/api/routes.py`
- `app/core/graph.py`
- `app/services/tools.py`
- `dashboard.py`
- `main.py`

## Runtime execution flow

### 1. Frontend emits a query

`dashboard.py` builds JSON with:

- `query`
- `filters`
- `thread_id`
- `tenant_id`

Then it opens an SSE stream to:

- `POST /api/v1/query`

### 2. FastAPI parses and authenticates

`app/api/routes.py -> handle_query()`

This step:

- parses `QueryRequest`
- authenticates the user
- determines `thread_id`
- prepares LangGraph `config`

### 3. Cache is checked

Before graph execution:

- Redis exact cache is checked
- Qdrant semantic cache is checked on miss

If either returns a hit:

- the graph is skipped
- SSE still streams back a final answer payload

### 4. Graph input is built

`handle_query()` creates:

- `messages = [("user", query)]`
- `tenant_id = current_user.tenant_id`

This is the starting `ResearchState`.

### 5. Graph executes

`request.app.state.graph.astream_events(...)`

The graph in `app/core/graph.py` contains:

- node `agent`
- node `tools`
- conditional routing through `tools_condition`

### 6. Agent node runs

`app/core/graph.py -> call_model()`

This function:

- injects the system prompt if needed
- ensures the prompt includes tenant-aware vault rules
- calls `llm_with_tools.ainvoke(messages)`

### 7. Tool node may run

If the model chooses a tool, LangGraph routes to `ToolNode`.

The tools live in `app/services/tools.py`.

Main tools:

- `rag_tool`
- `list_vault_papers_tool`
- `arxiv_search_tool`
- `auto_ingest_paper_tool`
- `python_repl_tool`

### 8. Tool output is fed back into the graph

After a tool finishes:

- the result is appended to graph state
- execution returns to the agent node

That is the core reason LangGraph exists in this app.

### 9. SSE translation happens

`handle_query()` listens to graph events and converts them into UI payloads:

- model token stream
- tool start event
- tool end event
- final metrics event

### 10. Background governance runs

After streaming completes:

- usage log is written
- trace log is written
- exact cache is updated
- semantic cache is updated

## Flow across files

`dashboard.py`
-> `app/api/routes.py: handle_query`
-> `app/core/cache.py`
-> `main.py: app.state.graph`
-> `app/core/graph.py: call_model`
-> `app/services/tools.py`
-> `app/services/vector_store.py` or `app/services/ingestion.py`
-> `app/api/routes.py: finalize_query_governance`

## What to rebuild first if doing this from scratch

1. Get a non-agent query route working.
2. Add one retrieval tool.
3. Move the prompt plus tool into LangGraph.
4. Add SSE streaming.
5. Add checkpoint persistence.
6. Add more tools.
7. Add governance and cache writes after response completion.

That keeps the complexity under control while preserving the eventual architecture.
