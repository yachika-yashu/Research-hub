import operator
import sqlite3
from typing import Annotated, List, TypedDict, Union, Dict, Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

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
    The main brain of the research agent. 
    It takes the context and decides whether to query the vault, search Arxiv, or just answer.
    """
    messages = state["messages"]
    # Inject system instruction if it's the first message
    if not any(isinstance(m, SystemMessage) for m in messages):
        tenant_id = state.get("tenant_id", "default")
        system_msg = SystemMessage(content=(
            "You are Hanuman, a Research Intelligence Assistant. "
            f"Your current Research Vault ID is: {tenant_id}. "
            "You have access to a local research vault (internal database), Arxiv search, and a Python Sandbox. "
            "IMPORTANT: All papers the user uploads are stored in the local vault. "
            "VAULT AUTHORITY: If the user asks for the name of the paper, what was just uploaded, or refers to 'the paper', "
            "you MUST call list_vault_papers_tool first to identify the correct file by its upload date. "
            "Do NOT use semantic search (rag_tool) for identifying which file exists in the vault. "
            "DEEP DISCOVERY: If you are looking for a specific Figure, Diagram, or Detail and cannot find it "
            "in the first RAG search, perform a SECOND search using the filename + specific keywords like 'Figure Content' or 'Table Data'. "
            "If a retrieved context chunk contains an [IMAGE_REFERENCE: URL] tag, you should display that image "
            "in your final response using standard markdown: ![Figure](URL). This helps the user visualize the research. "
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
