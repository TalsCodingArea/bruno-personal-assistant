"""Expandable general-capability graph for safe operational maintenance."""

from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

GENERAL_SYSTEM_PROMPT = """You are Bruno's general operations agent.
Your first and currently only maintenance capability repairs duplicate Active operational-context
versions in Notion. When the user mentions multiple current operational versions, the
expense-checkup conflict, or asks you to repair operational context, call
repair_duplicate_operational_context_versions. The tool audits every operational key, keeps the
newest business observation, links it to the stale siblings, and supersedes those siblings.
Report exactly what the tool confirmed. If it reports healthy, say no duplicates were found.
Never claim a repair before the tool succeeds. For unrelated general requests, briefly state
that the capability is not available yet. Keep the response concise."""


GeneralAgentGraph = CompiledStateGraph[
    MessagesState,
    None,
    MessagesState,
    MessagesState,
]


def build_general_agent_graph(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> GeneralAgentGraph:
    """Build the general graph; future capabilities expand through its tool catalog."""

    bound_model = model.bind_tools(list(tools))

    async def assistant(state: MessagesState) -> dict[str, list[AIMessage]]:
        response = await bound_model.ainvoke(
            [SystemMessage(content=GENERAL_SYSTEM_PROMPT), *state["messages"]]
        )
        if not isinstance(response, AIMessage):
            raise TypeError("The general agent model must return an AIMessage")
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("assistant", assistant)
    builder.add_node("tools", ToolNode(list(tools)))
    builder.add_edge(START, "assistant")
    builder.add_conditional_edges("assistant", tools_condition, {"tools": "tools", END: END})
    builder.add_edge("tools", "assistant")
    return builder.compile(checkpointer=checkpointer)


async def run_general_turn(
    graph: GeneralAgentGraph,
    *,
    thread_id: str,
    message: str,
    callbacks: Sequence[Any] = (),
) -> str:
    """Run one persistent general-agent turn and return its final text."""

    if not message.strip():
        raise ValueError("message cannot be empty")
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 6,
    }
    if callbacks:
        config["callbacks"] = list(callbacks)
    state = await graph.ainvoke(
        {"messages": [HumanMessage(content=message)]},
        config=config,
    )
    return _result_text(state)


def _result_text(state: Mapping[str, Any]) -> str:
    messages = state.get("messages")
    if not isinstance(messages, list) or not messages:
        raise TypeError("General agent returned no messages")
    response = messages[-1]
    if not isinstance(response, AIMessage) or not response.text.strip():
        raise TypeError("General agent did not finish with text")
    return response.text.strip()
