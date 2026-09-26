"""Lifecycle and capability adapters for the Bruno shell."""

import asyncio
from contextlib import AbstractAsyncContextManager
from typing import Any, cast

from financial_agent.bootstrap import FinanceApplication, build_finance_application
from financial_agent.config import Settings, get_settings
from financial_agent.graphs.conversation import (
    ConversationGraph,
    build_conversation_graph,
    conversation_config,
    resume_approval,
    start_conversation_turn,
)
from financial_agent.integrations.openai_model import build_openai_chat_model
from financial_agent.services.expense_automation import ExpenseAutomationService
from financial_agent.tools.automation import (
    build_expense_automation_tools,
    build_expense_checkup_tools,
)
from financial_agent.tools.automation.monitoring import DailyBudgetGraphRunner
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from bruno.config import BrunoSettings
from bruno.coordinator import CapabilityName, build_coordinator_graph
from bruno.debounce import CheckupDebouncer


class AutomationToolNotFoundError(ValueError):
    """Raised when the trusted automation registry has no matching tool."""


class BrunoRuntime:
    """Own finance dependencies, durable checkpoints, and trusted tools."""

    def __init__(
        self,
        settings: BrunoSettings,
        *,
        finance_settings: Settings | None = None,
    ) -> None:
        self.settings = settings
        self.finance_settings = finance_settings or get_settings()
        self.debouncer = CheckupDebouncer(settings.expense_checkup_delay_seconds)
        self._lock = asyncio.Lock()
        self._started = False
        self._saver_context: AbstractAsyncContextManager[AsyncSqliteSaver] | None = None
        self._saver: AsyncSqliteSaver | None = None
        self.finance: FinanceApplication | None = None
        self.finance_graph: ConversationGraph | None = None
        self.coordinator_graph: Any | None = None
        self.expenses: ExpenseAutomationService | None = None
        self.automation_tools: dict[str, Any] = {}

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            self.settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            saver_context = AsyncSqliteSaver.from_conn_string(
                str(self.settings.checkpoint_path)
            )
            saver = await saver_context.__aenter__()
            await saver.setup()
            try:
                model = build_openai_chat_model(self.finance_settings)
                if self.finance_settings.model_api_key is None:
                    raise ValueError("OPENAI_API_KEY is required")
                router_model = ChatOpenAI(
                    model=self.settings.router_model,
                    api_key=self.finance_settings.model_api_key,
                    use_responses_api=True,
                    output_version="responses/v1",
                    reasoning={"effort": "low"},
                    max_retries=2,
                    timeout=30.0,
                )
                finance = build_finance_application(
                    self.finance_settings,
                    mutation_review_model=model,
                )
                graph = build_conversation_graph(
                    model,
                    finance.tools.approval_conversation,
                    saver,
                    currency=self.finance_settings.currency,
                    profile_service=finance.profile,
                )
                coordinator = build_coordinator_graph(router_model, saver)
                source_ids = self.finance_settings.require_notion_data_source_ids()
                expenses = ExpenseAutomationService(
                    finance.notion,
                    source_ids["expenses"],
                    reader=finance.finance.reader,
                )
                tools = (
                    *build_expense_automation_tools(
                        expenses,
                        text_model=router_model,
                    ),
                    *build_expense_checkup_tools(
                        cast(DailyBudgetGraphRunner, finance.daily_budget_graph)
                    ),
                )
            except Exception:
                await saver_context.__aexit__(None, None, None)
                raise
            self._saver_context = saver_context
            self._saver = saver
            self.finance = finance
            self.finance_graph = graph
            self.coordinator_graph = coordinator
            self.expenses = expenses
            self.automation_tools = {tool.name: tool for tool in tools}
            self._started = True

    async def aclose(self) -> None:
        await self.debouncer.aclose()
        if self.finance is not None:
            await self.finance.aclose()
        if self._saver_context is not None:
            await self._saver_context.__aexit__(None, None, None)
        self._started = False

    async def finance_turn(
        self,
        chat_id: str,
        message: str,
        callbacks: list[Any],
    ) -> dict[str, Any]:
        await self.start()
        graph = self._require_graph()
        thread_id = f"telegram:{chat_id}:finance"
        snapshot = await graph.aget_state(conversation_config(thread_id))
        if snapshot.interrupts:
            decision = _approval_decision(message)
            if decision is None:
                return {"approval_request": snapshot.interrupts[0].value}
            result = await resume_approval(
                graph,
                thread_id=thread_id,
                approved=decision,
                callbacks=callbacks,
            )
        else:
            result = await start_conversation_turn(
                graph,
                thread_id=thread_id,
                message=message,
                callbacks=callbacks,
            )
        return {
            "output": result.response.text if result.response is not None else "",
            "approval_request": result.approval_request,
        }

    async def select_capability(self, chat_id: str, message: str) -> CapabilityName:
        """Resume pending finance work first; otherwise run the fast router node."""

        await self.start()
        finance_graph = self._require_graph()
        finance_thread = f"telegram:{chat_id}:finance"
        finance_state = await finance_graph.aget_state(
            conversation_config(finance_thread)
        )
        if finance_state.interrupts:
            return "finance"
        if self.coordinator_graph is None:
            raise RuntimeError("Coordinator graph is not started")
        state = await self.coordinator_graph.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config={
                "configurable": {"thread_id": f"telegram:{chat_id}:coordinator"}
            },
        )
        capability = state.get("selected_capability")
        if capability not in {"finance", "general"}:
            raise RuntimeError("Coordinator returned an unknown capability")
        return cast(CapabilityName, capability)

    async def invoke_automation(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        await self.start()
        tool = self.automation_tools.get(tool_name)
        if tool is None:
            raise AutomationToolNotFoundError(tool_name)
        return await tool.ainvoke(arguments)

    def _require_graph(self) -> ConversationGraph:
        if self.finance_graph is None:
            raise RuntimeError("Bruno runtime is not started")
        return self.finance_graph


def _approval_decision(message: str) -> bool | None:
    normalized = message.strip().casefold()
    if normalized in {"yes", "approve", "approved", "confirm", "y", "כן", "מאשר"}:
        return True
    if normalized in {"no", "reject", "rejected", "cancel", "n", "לא", "דוחה"}:
        return False
    return None
