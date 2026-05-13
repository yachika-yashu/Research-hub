import operator
from typing import Annotated, List, TypedDict

from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph import StateGraph, START
from langgraph.prebuilt import ToolNode, tools_condition

from app.services.tools import arxiv_search_tool, python_repl_tool, rag_tool, auto_ingest_paper_tool, list_vault_papers_tool
from app.core.config import GENERATION_MODEL, GENERATION_TEMP

# Define the state of the graph
class ResearchState(TypedDict):
    # messages: The history of the conversation, with add_messages-like behavior
    # We'll use Annotated to specify how to merge new messages
    messages: Annotated[List[BaseMessage], operator.add]
    # tenant_id: To ensure isolation across tools
    tenant_id: str

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
        system_msg = SystemMessage(content=(
            "You are Hanuman, a Research Intelligence Assistant. "
            f"Your current Research Vault ID is: {tenant_id}. "
            "You have access to a local research vault (internal database), Arxiv search, and a Python Sandbox. "
            "IMPORTANT: All papers the user uploads are stored in the local vault. "
            # list_vault_papers_tool gives exact filenames; rag_tool does semantic
            # search which can match wrong papers — don't use it to identify files.
            "VAULT AUTHORITY: If the user asks for the name of the paper, what was just uploaded, or refers to 'the paper', "
            "you MUST call list_vault_papers_tool first to identify the correct file by its upload date. "
            "Do NOT use semantic search (rag_tool) for identifying which file exists in the vault. "
            # Second-pass search recovers figures/tables that rank low in the first pass
            # because their chunk text is mostly the image markdown, not descriptive words.
            "DEEP DISCOVERY: If you are looking for a specific Figure, Diagram, or Detail and cannot find it "
            "in the first RAG search, perform a SECOND search using the filename + specific keywords like 'Figure Content' or 'Table Data'. "
            # Without this instruction the LLM paraphrases image markdown and drops
            # the URL, making figures invisible to the user in the Streamlit UI.
            "IMAGES: Context chunks may contain markdown images like ![Figure](URL). "
            "Always include these image tags verbatim in your response so the user can see the visual. "
            "Never omit or paraphrase image markdown — copy it exactly as-is. "
            "Always prioritize the local vault for precision. Cite your sources using [N] notation."
        ))
        messages = [system_msg] + messages

    response = await llm_with_tools.ainvoke(messages)
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
