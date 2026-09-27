"""General-agent maintenance capability and expansion seam."""

import asyncio
from collections.abc import Sequence
from typing import Any

from financial_agent.domain.operational_context import OperationalContextRepair
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver

from bruno.general import build_general_agent_graph, run_general_turn
from bruno.general_tools import build_general_tools


class Repairer:
    def __init__(self) -> None:
        self.calls = 0

    async def repair_duplicate_current_versions(
        self,
    ) -> tuple[OperationalContextRepair, ...]:
        self.calls += 1
        return (
            OperationalContextRepair(
                key="monitoring.runtime.2026-09.actual_overspend.faee050aa7e551ec",
                kept_page_id="latest-page",
                superseded_page_ids=("stale-page",),
            ),
        )


class GeneralRepairModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "general-repair-test-model"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if any(isinstance(message, ToolMessage) for message in messages):
            response = AIMessage(content="Repaired 1 duplicate operational version.")
        else:
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "repair_duplicate_operational_context_versions",
                        "id": "repair-1",
                        "args": {},
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=response)])


def test_general_agent_repairs_operational_duplicates_through_one_tool_seam() -> None:
    repairer = Repairer()
    graph = build_general_agent_graph(
        GeneralRepairModel(),
        build_general_tools(repairer),
        InMemorySaver(),
    )

    response = asyncio.run(
        run_general_turn(
            graph,
            thread_id="general-maintenance",
            message="Fix the multiple current operational versions.",
        )
    )

    assert response == "Repaired 1 duplicate operational version."
    assert repairer.calls == 1
