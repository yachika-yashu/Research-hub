# 05 LangGraph State Machine

## Why LangGraph is here

LangGraph is used because the query path is not a single linear prompt. The system needs a cyclic state machine:

- ask model what to do
- maybe call a tool
- feed tool output back into the model
- repeat until no more tool calls are needed

That pattern is exactly what LangGraph formalizes.

## State definition

`ResearchState` is a `TypedDict` with:

- `messages`
- `tenant_id`

`messages` is annotated with `operator.add`, which tells LangGraph how to merge new messages into state over successive node executions.

Why this matters:

- state evolution is declarative
- node outputs can append instead of replacing
- conversation memory persists naturally across turns

## Nodes

### `agent`

Function: `call_model(state)`

Responsibilities:

- ensure the system message exists
- include `tenant_id` instructions for tool isolation
- call the LLM with tool bindings
- return one new AI message

### `tools`

Built with `ToolNode(tools)`.

Responsibilities:

- inspect tool calls emitted by the model
- execute the correct Python tool
- add tool outputs back into state as messages

## Edges

The graph is:

1. `START -> agent`
2. `agent -> tools` when `tools_condition` detects tool calls
3. `agent -> END` when there are no tool calls
4. `tools -> agent` always

This is the minimal agent loop.

## Checkpointing

The graph is compiled with a checkpointer during FastAPI startup:

```python
async with AsyncSqliteSaver.from_conn_string("checkpoints.db") as memory:
    app.state.graph = compile_graph(memory)
```

What checkpointing changes:

- message history survives between requests
- thread state can be loaded by `thread_id`
- chat history endpoint can reconstruct user/assistant messages

Without checkpointing, the graph would forget prior turns once the request ended.

## Branching behavior

Branching is currently model-driven, not rule-heavy.

The key branch is:

- if model emits tool calls, go to `tools`
- otherwise terminate

The system prompt nudges specific tool behaviors, for example:

- use `list_vault_papers_tool` when identifying which uploaded paper the user means
- use `rag_tool` for vault retrieval

So the branch policy is partly encoded in prompt instructions and partly in graph structure.

## Loops

The important loop is:

`agent -> tools -> agent`

Why loops are valuable here:

- first retrieval may be insufficient
- the model may need to discover the right filename first
- the model can search arXiv, then ingest, then answer

This is more robust than a one-pass chain because the model can revise its plan after seeing tool outputs.

## State passing

State passes between nodes as a dictionary. Each node returns partial updates. LangGraph merges those updates according to the state schema rules.

In practical terms:

- input contains prior messages plus tenant context
- `agent` appends an AI message
- `tools` appends tool output messages
- next `agent` call sees everything so far

## Production implications

The current checkpointer backend is SQLite on local disk. That makes the graph state machine durable on one node, but not naturally shared across multiple API replicas.

If you rebuilt this for multi-instance production, the checkpoint backend should move to a shared store such as:

- PostgreSQL
- Redis-backed state adapter if supported
- managed database or object-backed checkpoint service

Otherwise thread continuity depends on routing repeat requests to the same machine.
