from __future__ import annotations

from typing import Any, Dict

from personal_assistant.agent.general.builder import build_agent
from personal_assistant.agent.capabilities.financial_advisor.graph import FinancialAdvisorRuntime, create_financial_advisor_graph
from personal_assistant.agent.general.llm import get_llm
from personal_assistant.agent.general.memory import MemoryStore
from personal_assistant.agent.general.uncategorized_workflow import create_uncategorized_review_graph

llm = get_llm()
memory = MemoryStore()
uncategorized_review_graph = create_uncategorized_review_graph()
financial_advisor_graph = create_financial_advisor_graph(llm)
financial_advisor_runtime = FinancialAdvisorRuntime(financial_advisor_graph)

# chat_id (str) -> job URL queued by the apply_for_job tool.
pending_jobs: Dict[str, str] = {}

# chat_id (str) -> (cached agent instance, whether it was built with the
# Notion MCP fallback tools). The flag lets us rebuild an agent that was
# created while the MCP server was unreachable once the server recovers --
# otherwise a startup hiccup would leave that chat without fallback tools
# for the whole process lifetime. Rebuilding is safe: conversation history
# lives in MemoryStore keyed by session, not on the agent instance.
agents: Dict[str, tuple[Any, bool]] = {}


async def get_or_build_agent(chat_id: str):
    """Return a cached agent for chat_id, creating one with bound tools on first use."""
    from personal_assistant.tools.registry import get_fallback_tools, get_workflow_tools

    # Read-only Notion MCP tools, shared across chats -- loaded once and
    # cached inside get_fallback_tools() itself, so this await is cheap
    # after the first successful call.
    fallback_tools = await get_fallback_tools()

    cached = agents.get(chat_id)
    if cached is not None:
        agent, built_with_fallback = cached
        if built_with_fallback or not fallback_tools:
            return agent
        # MCP server recovered since this agent was built -- fall through
        # and rebuild it with the fallback tools included.

    workflow_tools = get_workflow_tools(
        chat_id,
        pending_jobs,
        uncategorized_review_graph,
    )
    agent = build_agent(llm, memory, extra_tools=[*workflow_tools, *fallback_tools])
    agents[chat_id] = (agent, bool(fallback_tools))
    return agent


async def run_financial_advisor(chat_id: str, user_text: str) -> dict[str, Any]:
    """Run one finance turn through the dedicated financial advisor graph."""
    return await financial_advisor_runtime.ainvoke(
        {"input": user_text},
        config={"configurable": {"session_id": chat_id}},
    )
