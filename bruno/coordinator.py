"""One-node, context-aware capability selector for the Bruno shell."""

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

CapabilityName = Literal["finance", "general"]


class CapabilityDecision(BaseModel):
    capability: CapabilityName
    confidence: float = Field(ge=0, le=1)


class CoordinatorState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    active_capability: NotRequired[CapabilityName]
    selected_capability: NotRequired[CapabilityName]


def build_coordinator_graph(
    model: Any, checkpointer: BaseCheckpointSaver[Any]
) -> Any:
    """Build the deliberately tiny outer graph; capabilities retain their own graphs."""

    router = model.with_structured_output(
        CapabilityDecision,
        method="json_schema",
    )

    async def select_capability(
        state: CoordinatorState, config: RunnableConfig
    ) -> dict[str, CapabilityName]:
        recent = state["messages"][-8:]
        active = state.get("active_capability", "general")
        decision = await router.ainvoke(
            [
                SystemMessage(
                    content=(
                        "Route the newest message to exactly one capability. "
                        "finance handles expenses, income, budgets, affordability, savings, "
                        "bank balance, financial rules, recurring tasks, and follow-ups to "
                        "those discussions. general handles "
                        "everything else. Preserve the active capability for short or ambiguous "
                        "follow-ups unless the user clearly changes topic."
                    )
                ),
                HumanMessage(content=f"Active capability: {active}"),
                *recent,
            ],
            config=config,
        )
        capability = decision.capability
        return {
            "active_capability": capability,
            "selected_capability": capability,
        }

    graph = StateGraph(CoordinatorState)
    graph.add_node("select_capability", select_capability)
    graph.add_edge(START, "select_capability")
    graph.add_edge("select_capability", END)
    return graph.compile(checkpointer=checkpointer)
