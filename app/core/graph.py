import operator
from typing import Annotated, List, Optional, TypedDict

from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph import StateGraph, START
from langgraph.prebuilt import ToolNode, tools_condition

from app.services.tools import arxiv_search_tool, python_repl_tool, rag_tool, auto_ingest_paper_tool, list_vault_papers_tool
from app.core.config import GENERATION_MODEL, GENERATION_TEMP

# Define the state of the graph
class ResearchState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    tenant_id: str
    filename_filter: Optional[str]  # when set, scope all rag_tool calls to this paper

# Initialize the LLM
llm = ChatOpenAI(model=GENERATION_MODEL, temperature=GENERATION_TEMP)

# Define the tools
tools = [arxiv_search_tool, python_repl_tool, rag_tool, auto_ingest_paper_tool, list_vault_papers_tool]
# Bind tools to the LLM
llm_with_tools = llm.bind_tools(tools)

# Define the nodes
async def call_model(state: ResearchState):
    """
    Core LLM node. Prepends a system prompt on the first turn only (detected by
    absence of a SystemMessage in state), then invokes the LLM with all tools bound.
    The system prompt is NOT stored in state — it's rebuilt each turn from scratch
    so it always reflects the current tenant_id without bloating the checkpoint.
    """
    messages = state["messages"]
    if not any(isinstance(m, SystemMessage) for m in messages):
        tenant_id = state.get("tenant_id", "default")
        filename_filter = state.get("filename_filter")

        scope_instruction = (
            f"PAPER SCOPE: The user has scoped this conversation to a single paper: '{filename_filter}'. "
            f"You MUST pass filename='{filename_filter}' to every rag_tool call. "
            "Do NOT search other papers. If asked 'what is the name of this paper', the answer is that filename."
        ) if filename_filter else (
            "VAULT AUTHORITY: If the user asks for the name of the paper, what was just uploaded, or refers to 'the paper', "
            "you MUST call list_vault_papers_tool first to identify the correct file by its upload date. "
            "Do NOT use semantic search (rag_tool) for identifying which file exists in the vault."
        )

        system_msg = SystemMessage(content=(
            "You are Hanuman, a Research Intelligence Assistant. "
            f"Your current Research Vault ID is: {tenant_id}. "
            "You have access to a local research vault (internal database), Arxiv search, and a Python Sandbox. "
            "IMPORTANT: All papers the user uploads are stored in the local vault. "
            f"{scope_instruction} "
            "DEEP DISCOVERY: If you are looking for a specific Figure, Diagram, or Detail and cannot find it "
            "in the first RAG search, perform a SECOND search using the filename + specific keywords like 'Figure Content' or 'Table Data'. "
            "IMAGES: Context chunks may contain markdown images like ![Figure](URL). "
            "Always include these image tags verbatim in your response so the user can see the visual. "
            "Never omit or paraphrase image markdown — copy it exactly as-is. "
            "Always prioritize the local vault for precision. Cite your sources using [N] notation."
        ))
        messages = [system_msg] + messages

    response = await llm_with_tools.ainvoke(messages)

    # Enforce tenant_id and filename_filter in every vault tool call.
    # The LLM may omit tenant_id or filename even when instructed — patching here
    # guarantees the correct values reach search_vdb regardless of LLM behavior.
    _tenant_id = state.get("tenant_id", "default")
    _filename_filter = state.get("filename_filter")
    print(f"GRAPH: call_model — filename_filter={_filename_filter!r}, tool_calls={[tc['name'] for tc in response.tool_calls] if response.tool_calls else []}")
    if response.tool_calls:
        patched = []
        for tc in response.tool_calls:
            if tc["name"] in ("rag_tool", "auto_ingest_paper_tool", "list_vault_papers_tool"):
                args = dict(tc.get("args", {}))
                args["tenant_id"] = _tenant_id
                if tc["name"] == "rag_tool" and _filename_filter:
                    args["filename"] = _filename_filter
                patched.append({**tc, "args": args})
            else:
                patched.append(tc)
        response = response.model_copy(update={"tool_calls": patched})

    return {"messages": [response]}

# Define the Tool Node
tool_node = ToolNode(tools)

# Build the Graph
workflow = StateGraph(ResearchState)

# Add Nodes
workflow.add_node("agent", call_model)
workflow.add_node("tools", tool_node)

# Add Edges
workflow.add_edge(START, "agent")

# Use tools_condition to decide whether to continue to tools or end
workflow.add_conditional_edges(
    "agent",
    tools_condition,
)

# After tools, always return to the agent to summarize or take next steps
workflow.add_edge("tools", "agent")

# Function to compile graph (called by lifespan)
def compile_graph(checkpointer):
    return workflow.compile(checkpointer=checkpointer)

# Default graph for backward compatibility (may fail for async persistence if used without lifespan)
graph = workflow.compile()
