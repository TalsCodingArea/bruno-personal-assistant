"""Tests for the single-agent graph and its thread memory."""

import asyncio
from collections.abc import Sequence
from datetime import date
from typing import Any, ClassVar

from financial_agent.graphs import (
    ContextPolicy,
    build_read_only_conversation_graph,
    resume_profile_write,
    send_message,
    start_conversation_turn,
)
from financial_agent.integrations.notion_profile import NotionFinancialProfileRepository
from financial_agent.services.budget_planning import BudgetPlanningService
from financial_agent.services.interaction import InteractionProfileService
from financial_agent.services.profile import FinancialProfileService
from financial_agent.tools.write import (
    build_budget_write_tools,
    build_interaction_write_tools,
    build_profile_write_tools,
)
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver

from tests.fakes import FakeFinanceReader, FakeNotion
from tests.test_budget_planning import CreationRepository, Rules


class CountingChatModel(BaseChatModel):
    """Offline model that reports how many user turns exist in checkpointed state."""

    @property
    def _llm_type(self) -> str:
        return "counting-test-model"

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
        count = sum(isinstance(message, HumanMessage) for message in messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=f"turns={count}"))])


class PromptChatModel(CountingChatModel):
    """Echo the architectural system prompt for deterministic policy assertions."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        prompt = "\n".join(
            str(message.content)
            for message in messages
            if isinstance(message, SystemMessage)
        )
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=prompt))]
        )


class StructuredSummaryChatModel(CountingChatModel):
    """Return Responses-API-style content blocks only for compaction calls."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if messages and isinstance(messages[0], SystemMessage) and (
            "Compact the older part" in str(messages[0].content)
        ):
            message = AIMessage(
                content=[{"type": "text", "text": "Older objective retained."}]
            )
        else:
            message = AIMessage(content="Current answer")
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_same_thread_retains_messages_and_different_thread_is_isolated() -> None:
    graph = build_read_only_conversation_graph(
        CountingChatModel(),
        [],
        InMemorySaver(),
        today=date(2026, 8, 21),
    )

    async def run() -> tuple[str, str, str]:
        first = await send_message(graph, thread_id="conversation-a", message="First")
        second = await send_message(graph, thread_id="conversation-a", message="Second")
        isolated = await send_message(graph, thread_id="conversation-b", message="First")
        return str(first.content), str(second.content), str(isolated.content)

    assert asyncio.run(run()) == ("turns=1", "turns=2", "turns=1")


def test_assistant_receives_strict_ils_currency_policy() -> None:
    graph = build_read_only_conversation_graph(
        PromptChatModel(),
        [],
        InMemorySaver(),
        today=date(2026, 8, 21),
    )

    response = asyncio.run(send_message(graph, thread_id="ils", message="Budget?"))

    assert "every unlabeled monetary amount is ILS" in str(response.content)
    assert "Display money with the ₪ symbol" in str(response.content)
    assert "get_budget_planning_context" in str(response.content)
    assert "always call get_current_reimbursement" in str(response.content)
    assert "always call get_current_credit_debt" in str(response.content)
    assert "never report credit debt\nnet of reimbursement" in str(response.content)
    assert "income minus all Budget pages" in str(response.content)
    assert "no more than three months" in str(response.content)
    assert "Progressive describes how a budget is consumed" in str(response.content)
    assert "Future-expense allocations must be Discrete" in str(response.content)
    assert "review recent actual spending" in str(response.content)
    assert "get_monthly_summary" in str(response.content)
    assert "get_budget_status" in str(response.content)
    assert "does not need pre-approval" in str(response.content)
    assert "Use check_expenses" in str(response.content)
    assert "Be blunt and decisive" in str(response.content)


def test_context_separates_interaction_settings_from_financial_rules() -> None:
    notion = FakeNotion(
        {
            "profile": [
                {
                    "id": "banter",
                    "created_time": "2026-08-20T09:00:00.000Z",
                    "last_edited_time": "2026-08-22T09:00:00.000Z",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"plain_text": "Assistant Banter"}],
                        },
                        "Key": {
                            "type": "rich_text",
                            "rich_text": [{"plain_text": "assistant.banter"}],
                        },
                        "Kind": {
                            "type": "select",
                            "select": {"name": "Preference"},
                        },
                        "Scope": {
                            "type": "multi_select",
                            "multi_select": [{"name": "Conversation"}],
                        },
                        "Statement": {
                            "type": "rich_text",
                            "rich_text": [{"plain_text": "playful"}],
                        },
                        "Status": {
                            "type": "status",
                            "status": {"name": "Active"},
                        },
                        "Supersedes": {"type": "relation", "relation": []},
                        "Operation ID": {
                            "type": "unique_id",
                            "unique_id": {"prefix": "CTX", "number": 10},
                        },
                    },
                }
            ]
        }
    )
    profile = FinancialProfileService(NotionFinancialProfileRepository(notion, "profile"))
    graph = build_read_only_conversation_graph(
        PromptChatModel(),
        [],
        InMemorySaver(),
        today=date(2026, 8, 21),
        profile_service=profile,
    )

    async def run() -> tuple[str, list[dict[str, object]]]:
        response = await send_message(graph, thread_id="interaction", message="Hello")
        snapshot = await graph.aget_state(
            {"configurable": {"thread_id": "interaction"}}
        )
        return str(response.content), snapshot.values["financial_profile"]

    prompt, financial_profile = asyncio.run(run())

    assert '"banter":"playful"' in prompt
    assert financial_profile == []


def test_context_node_summarizes_old_turns_and_removes_their_messages() -> None:
    graph = build_read_only_conversation_graph(
        CountingChatModel(),
        [],
        InMemorySaver(),
        today=date(2026, 8, 21),
        context_policy=ContextPolicy(
            compact_after_tokens=1,
            keep_recent_user_turns=1,
        ),
    )

    async def run() -> tuple[str, list[BaseMessage]]:
        await send_message(graph, thread_id="compact", message="First long topic")
        await send_message(graph, thread_id="compact", message="Second topic")
        snapshot = await graph.aget_state({"configurable": {"thread_id": "compact"}})
        return snapshot.values["conversation_summary"], snapshot.values["messages"]

    summary, messages = asyncio.run(run())

    assert summary == "turns=2"
    assert len(messages) == 2
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "Second topic"


def test_context_node_accepts_structured_text_from_responses_api() -> None:
    graph = build_read_only_conversation_graph(
        StructuredSummaryChatModel(),
        [],
        InMemorySaver(),
        today=date(2026, 8, 21),
        context_policy=ContextPolicy(
            compact_after_tokens=1,
            keep_recent_user_turns=1,
        ),
    )

    async def run() -> str:
        await send_message(graph, thread_id="structured-summary", message="First topic")
        await send_message(graph, thread_id="structured-summary", message="Second topic")
        snapshot = await graph.aget_state(
            {"configurable": {"thread_id": "structured-summary"}}
        )
        return snapshot.values["conversation_summary"]

    assert asyncio.run(run()) == "Older objective retained."


class ApprovalChatModel(CountingChatModel):
    """Request one profile write, then acknowledge its ToolMessage."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if any(isinstance(message, ToolMessage) for message in messages):
            message = AIMessage(content="Profile write finished")
        else:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "apply_financial_profile_update",
                        "id": "profile-write-1",
                        "args": {
                            "name": "Groceries split",
                            "key": "spending.groceries.split",
                            "kind": "Preference",
                            "scopes": ["Spending"],
                            "statement": "Use 50% for Tal's budget.",
                            "rationale": "Shared equally.",
                        },
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


class InteractionApprovalChatModel(CountingChatModel):
    """Request one guarded interaction update, then acknowledge the tool result."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if any(isinstance(message, ToolMessage) for message in messages):
            message = AIMessage(content="Interaction preference saved")
        else:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "apply_interaction_preference_update",
                        "id": "interaction-write-1",
                        "args": {
                            "setting": "banter",
                            "value": "playful",
                            "rationale": "Tal requested playful banter.",
                        },
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


class BudgetApprovalChatModel(CountingChatModel):
    """Request one guarded Budget page plan, then acknowledge creation."""

    source_fingerprint: ClassVar[str] = ""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if any(isinstance(message, ToolMessage) for message in messages):
            message = AIMessage(content="Budget pages created")
        else:
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "apply_monthly_budget_plan",
                        "id": "budget-write-1",
                        "args": {
                            "month": "2026-09",
                            "source_fingerprint": self.source_fingerprint,
                            "financial_cap": "1500",
                            "cap_basis": "user_provided",
                            "cap_rationale": "Tal supplied the cap.",
                            "items": [
                                {
                                    "subcategory": "Rent",
                                    "amount": "1000",
                                    "progressive": "Accumulated",
                                    "volatility_percent": "0",
                                    "purpose": "regular",
                                    "rationale": "Stable rent allocation.",
                                }
                            ],
                        },
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


def test_profile_write_interrupts_before_notion_and_runs_only_after_approval() -> None:
    notion = FakeNotion({"profile": []})
    profile = FinancialProfileService(NotionFinancialProfileRepository(notion, "profile"))
    graph = build_read_only_conversation_graph(
        ApprovalChatModel(),
        build_profile_write_tools(profile),
        InMemorySaver(),
        today=date(2026, 8, 21),
        profile_service=profile,
    )

    async def run() -> tuple[bool, str]:
        pending = await start_conversation_turn(
            graph, thread_id="approval", message="Save this preference"
        )
        assert notion.created == []
        completed = await resume_profile_write(
            graph, thread_id="approval", approved=True
        )
        assert completed.response is not None
        return pending.requires_approval, str(completed.response.content)

    requires_approval, response = asyncio.run(run())

    assert requires_approval is True
    assert response == "Profile write finished"
    assert len(notion.created) == 1


def test_interaction_write_uses_the_same_mandatory_approval_boundary() -> None:
    notion = FakeNotion({"profile": []})
    profile = FinancialProfileService(NotionFinancialProfileRepository(notion, "profile"))
    interaction = InteractionProfileService(profile)
    graph = build_read_only_conversation_graph(
        InteractionApprovalChatModel(),
        build_interaction_write_tools(interaction),
        InMemorySaver(),
        today=date(2026, 8, 21),
        profile_service=profile,
    )

    async def run() -> tuple[bool, str]:
        pending = await start_conversation_turn(
            graph, thread_id="interaction-approval", message="Use playful banter"
        )
        assert notion.created == []
        completed = await resume_profile_write(
            graph, thread_id="interaction-approval", approved=True
        )
        assert completed.response is not None
        return pending.requires_approval, str(completed.response.content)

    requires_approval, response = asyncio.run(run())

    assert requires_approval is True
    assert response == "Interaction preference saved"
    assert notion.created[0]["properties"]["Key"]["rich_text"][0]["text"]["content"] == (
        "assistant.banter"
    )


def test_budget_page_creation_runs_autonomously_and_returns_confirmation() -> None:
    finance = FakeFinanceReader()
    repository = CreationRepository()
    service = BudgetPlanningService(finance, Rules(), repository)
    BudgetApprovalChatModel.source_fingerprint = asyncio.run(
        service.planning_context(date(2026, 9, 1))
    ).source_fingerprint
    graph = build_read_only_conversation_graph(
        BudgetApprovalChatModel(),
        build_budget_write_tools(service),
        InMemorySaver(),
        today=date(2026, 8, 27),
    )

    async def run() -> tuple[bool, str]:
        completed = await start_conversation_turn(
            graph, thread_id="budget-approval", message="Create September's budget"
        )
        assert completed.response is not None
        return completed.requires_approval, str(completed.response.content)

    requires_approval, response = asyncio.run(run())

    assert requires_approval is False
    assert response == "Budget pages created"
    assert len(repository.calls) == 1
