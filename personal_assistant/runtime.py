from __future__ import annotations

from typing import Any, Dict

from agent.builder import build_agent
from agent.financial_advisor_graph import FinancialAdvisorRuntime, create_financial_advisor_graph
from agent.llm import get_llm
from agent.memory import MemoryStore
from agent.uncategorized_workflow import create_uncategorized_review_graph

llm = get_llm()
memory = MemoryStore()
uncategorized_review_graph = create_uncategorized_review_graph()
financial_advisor_graph = create_financial_advisor_graph(llm)
financial_advisor_runtime = FinancialAdvisorRuntime(financial_advisor_graph)

# chat_id (str) -> job URL queued by the apply_for_job tool.
pending_jobs: Dict[str, str] = {}

# chat_id (str) -> cached RunnableWithMessageHistory agent instance.
agents: Dict[str, Any] = {}


def get_or_build_agent(chat_id: str):
    """Return a cached agent for chat_id, creating one with bound tools on first use."""
    if chat_id not in agents:
        from tools.registry import get_workflow_tools

        workflow_tools = get_workflow_tools(
            chat_id,
            pending_jobs,
            uncategorized_review_graph,
        )
        agents[chat_id] = build_agent(llm, memory, extra_tools=workflow_tools)
    return agents[chat_id]


async def run_financial_advisor(chat_id: str, user_text: str) -> dict[str, Any]:
    """Run one finance turn through the dedicated financial advisor graph."""
    return await financial_advisor_runtime.ainvoke(
        {"input": user_text},
        config={"configurable": {"session_id": chat_id}},
    )
