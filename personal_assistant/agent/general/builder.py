from __future__ import annotations

from typing import Annotated, Any, Sequence, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from personal_assistant.agent.general.memory import MemoryStore
from personal_assistant.agent.general.system_prompt import SYSTEM_PROMPT


class GeneralAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


class GeneralAgentRuntime:
    """LangGraph-backed assistant runtime with the old ainvoke contract."""

    def __init__(self, graph, memory_store: MemoryStore) -> None:
        self._graph = graph
        self._memory_store = memory_store

    async def ainvoke(self, inputs: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        config = config or {}
        session_id = str(config.get("configurable", {}).get("session_id", "default"))
        user_text = str(inputs.get("input", ""))

        history = self._memory_store.get_history(session_id)
        starting_messages = [*history.messages, HumanMessage(content=user_text)]
        state = await self._graph.ainvoke({"messages": starting_messages}, config=config)

        messages = state.get("messages", [])
        final_ai = next((message for message in reversed(messages) if isinstance(message, AIMessage)), None)
        output = str(final_ai.content) if final_ai else ""

        history.add_user_message(user_text)
        if output:
            history.add_ai_message(output)

        return {"output": output}


def _build_graph(llm, tools: Sequence[Any]):
    model = llm.bind_tools(list(tools))
    tool_node = ToolNode(list(tools))

    async def agent_node(state: GeneralAgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
        response = await model.ainvoke(
            [
                SystemMessage(content=SYSTEM_PROMPT.strip()),
                *state["messages"],
            ],
            config=config,
        )
        return {"messages": [response]}

    graph = StateGraph(GeneralAgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


def build_agent(llm, memory_store: MemoryStore, extra_tools=None):
    from personal_assistant.tools.registry import get_tools

    tools = get_tools()
    if extra_tools:
        tools = tools + list(extra_tools)

    return GeneralAgentRuntime(_build_graph(llm, tools), memory_store)
