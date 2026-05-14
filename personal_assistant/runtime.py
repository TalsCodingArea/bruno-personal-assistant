from __future__ import annotations

from typing import Any, Dict

from agent.budget_workflow import (
    async_continue_budget_review,
    async_continue_budget_workflow,
    async_start_budget_review,
    async_start_budget_workflow,
    create_budget_graph,
    create_budget_review_graph,
)
from agent.builder import build_agent
from agent.llm import get_llm
from agent.memory import MemoryStore

llm = get_llm()
memory = MemoryStore()
budget_graph = create_budget_graph(llm)
budget_review_graph = create_budget_review_graph(llm)

# chat_id (str) -> LangGraph thread_id for the active budget session.
budget_sessions: Dict[str, str] = {}

# chat_id (str) -> LangGraph thread_id for the active budget review session.
budget_review_sessions: Dict[str, str] = {}

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
            budget_graph,
            budget_sessions,
            pending_jobs,
            budget_review_graph,
            budget_review_sessions,
        )
        agents[chat_id] = build_agent(llm, memory, extra_tools=workflow_tools)
    return agents[chat_id]
