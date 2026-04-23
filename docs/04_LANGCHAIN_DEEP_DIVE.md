# 04 LangChain Deep Dive

## What LangChain is doing here

LangChain is not used for a large chain registry in this project. It is used in a narrower but important role:

- `ChatOpenAI` provides the model wrapper
- `@tool` wraps callable capabilities into model-usable tools
- LangChain message classes carry conversation state
- tool binding enables OpenAI function-calling style interaction

The orchestration shell around those pieces is LangGraph.

## LLM object lifecycle

`app/core/graph.py` creates:

```python
llm = ChatOpenAI(model=GENERATION_MODEL, temperature=GENERATION_TEMP)
llm_with_tools = llm.bind_tools(tools)
```

What `bind_tools()` does:

- publishes tool schemas to the model wrapper
- allows the model to return structured tool call requests
- keeps tool dispatch separate from prompt construction

At runtime, `call_model()` sends the current message list into:

```python
response = await llm_with_tools.ainvoke(messages)
```

That response can be:

- a normal AI message with text
- an AI message containing tool call instructions

## Prompt flow

The effective prompt is message-based, not a single string template.

Sequence:

1. existing checkpointed messages are loaded
2. if no `SystemMessage` exists, one is injected
3. new user message is included
4. prior tool outputs may already be in message history
5. `ainvoke()` sends the message list to the chat model

Why this matters:

- the graph relies on accumulated messages as state
- tool results become part of the next model decision
- behavior is controlled more by conversation state than by a single prompt template

## `.invoke()` vs `.run()`

### `.invoke()`

Modern LangChain runnable interface.

- accepts structured input
- returns structured output
- works consistently across chains, models, and tools
- supports async variant `.ainvoke()`

This codebase uses `ainvoke()` on the model.

### `.run()`

Older convenience API, usually string-in string-out.

- less explicit
- less general
- weaker fit for structured tool-enabled interactions

This codebase still uses `.run()` indirectly in utility wrappers:

- `arxiv_wrapper.run(query)`
- `python_repl.run(code)`

That is fine there because those utilities are simple string interfaces, but for model orchestration `.invoke()` is the better abstraction.

## LLM call lifecycle

For one graph turn, the model call lifecycle is:

1. gather message history from state
2. inject system message if needed
3. serialize messages into provider format
4. send request to OpenAI chat completions API through `ChatOpenAI`
5. receive streamed chunks or final tool-call-bearing message
6. convert provider payload back into LangChain `AIMessage`
7. return it to LangGraph state

Why the model is low temperature:

- `GENERATION_TEMP = 0.1`
- reduces drift and unsupported speculation
- better fit for citation-grounded research assistant behavior

## Tool binding internals

Each `@tool` function becomes a schema the model can target by name. When the model decides to call `rag_tool`, the response includes:

- tool name
- arguments

LangGraph's `ToolNode` interprets that and executes the Python function. The result is appended to the message history as tool output, and the agent node runs again.

This is why the system can do multi-step behavior like:

1. ask vault what papers exist
2. inspect result
3. query a specific file
4. summarize findings

without custom hard-coded route logic for each branch.

## Why LangChain was chosen

In this codebase, LangChain is used because it standardizes:

- provider access through `ChatOpenAI`
- tool schemas
- message typing

Why that helps:

- less provider-specific glue code
- easier transition from plain chat to tool-using agent
- smoother LangGraph integration

## What would break if removed

Without the LangChain layer, the team would need to hand-build:

- chat message conversion
- tool schema publication to the model
- tool call parsing
- model wrapper integration with LangGraph

That is possible, but would materially increase orchestration code.
