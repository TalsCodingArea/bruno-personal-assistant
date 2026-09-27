"""Lifecycle and capability adapters for the Bruno shell."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import date, datetime
from decimal import Decimal
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
from financial_agent.integrations.jev import JevTransactionClassifier
from financial_agent.integrations.jev_category_decider import JevCategoryDecider
from financial_agent.integrations.openai_model import build_openai_chat_model
from financial_agent.integrations.tavily_search import TavilyMerchantSearch
from financial_agent.services.expense_automation import ExpenseAutomationService
from financial_agent.services.expense_classification import (
    ExpenseClassificationPolicy,
    ExpenseClassificationReader,
    ExpenseClassificationService,
)
from financial_agent.tools.automation import (
    build_expense_automation_tools,
    build_expense_checkup_tools,
)
from financial_agent.tools.automation.monitoring import DailyBudgetGraphRunner
from financial_agent.tools.serialization import jsonable
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from bank_account.automation import build_bank_account_automation_tools
from bank_account.config import load_bank_account_settings
from bruno.config import BrunoSettings
from bruno.coordinator import CapabilityName, build_coordinator_graph
from bruno.debounce import CheckupDebouncer
from bruno.scheduling import RecurringTask, SQLiteAssistantStore, scheduler_loop


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
        self.assistant_store = SQLiteAssistantStore(settings.scheduler_path)
        self._lock = asyncio.Lock()
        self._started = False
        self._saver_context: AbstractAsyncContextManager[AsyncSqliteSaver] | None = None
        self._saver: AsyncSqliteSaver | None = None
        self.finance: FinanceApplication | None = None
        self.finance_graph: ConversationGraph | None = None
        self.coordinator_graph: Any | None = None
        self.expenses: ExpenseAutomationService | None = None
        self.expense_classifier: ExpenseClassificationService | None = None
        self.automation_tools: dict[str, Any] = {}
        self._scheduler_task: asyncio.Task[None] | None = None

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
                    recurring_tasks=self.assistant_store,
                )
                graph = build_conversation_graph(
                    model,
                    (
                        *finance.tools.approval_conversation,
                        *build_expense_checkup_tools(
                            cast(DailyBudgetGraphRunner, finance.daily_budget_graph)
                        ),
                    ),
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
                if self.finance_settings.notion_token is None:
                    raise ValueError("FINANCE_AGENT_NOTION_TOKEN is required")
                bank_settings = load_bank_account_settings(require_notion=False)
                transaction_classifier = (
                    JevTransactionClassifier(
                        api_key=self.settings.typesafe_api_key,
                        model=self.settings.jev_model,
                        threshold=self.settings.jev_transaction_threshold,
                    )
                    if self.settings.typesafe_api_key is not None
                    else None
                )
                category_decider = (
                    JevCategoryDecider(
                        api_key=self.settings.typesafe_api_key,
                        model=self.settings.jev_model,
                    )
                    if self.settings.typesafe_api_key is not None
                    else None
                )
                merchant_search = (
                    TavilyMerchantSearch(self.settings.tavily_api_key)
                    if self.settings.tavily_api_key is not None
                    else None
                )
                expense_classifier = ExpenseClassificationService(
                    cast(ExpenseClassificationReader, finance.finance.reader),
                    expenses,
                    decider=category_decider,
                    search=merchant_search,
                    policy=ExpenseClassificationPolicy(
                        mode=self.settings.expense_classifier_mode,
                        confidence_threshold=Decimal(
                            str(self.settings.expense_classification_threshold)
                        ),
                    ),
                )
                tools = (
                    *build_expense_automation_tools(
                        expenses,
                        text_model=router_model,
                        transaction_classifier=transaction_classifier,
                    ),
                    *build_expense_checkup_tools(
                        cast(DailyBudgetGraphRunner, finance.daily_budget_graph)
                    ),
                    *build_bank_account_automation_tools(
                        bank_settings,
                        self.finance_settings.notion_token.get_secret_value(),
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
            self.expense_classifier = expense_classifier
            self.automation_tools = {tool.name: tool for tool in tools}
            self._started = True

    async def aclose(self) -> None:
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            await asyncio.gather(self._scheduler_task, return_exceptions=True)
            self._scheduler_task = None
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
            "deferred_notices": await self.assistant_store.pop_notices(chat_id),
        }

    async def analyze_created_expense(self, page_id: str) -> dict[str, str]:
        """Run the exact event monitor for one newly-created expense page."""

        await self.start()
        if self.finance is None:
            raise RuntimeError("Finance runtime is not started")
        event_id = f"expense-created:{page_id}"
        state = await self.finance.expense_monitor_graph.ainvoke(
            {
                "event_id": event_id,
                "notion_page_id": page_id,
                "observed_at": datetime.now().astimezone().isoformat(),
                "event_type": "created",
            }
        )
        decision = state.get("decision")
        if decision is None:
            raise RuntimeError("Expense monitor returned no decision")
        return {
            "severity": decision.highest_severity.value,
            "summary": decision.summary,
        }

    async def classify_created_expense(self, page_id: str) -> dict[str, Any]:
        """Classify one new expense before downstream budget analysis."""

        await self.start()
        if self.expense_classifier is None:
            raise RuntimeError("Expense classifier is not started")
        outcome = await self.expense_classifier.classify_created_expense(
            page_id,
            as_of=datetime.now().astimezone().date(),
        )
        serialized = jsonable(outcome)
        if not isinstance(serialized, dict):
            raise TypeError("Expense classifier returned an invalid result")
        return serialized

    async def current_account_outlook(self) -> dict[str, Any] | None:
        """Return today's account settlement outlook when bank access is configured."""

        await self.start()
        if self.finance is None or self.finance.cashflow is None:
            return None
        today = date.today()
        result = await self.finance.cashflow.account_outlook(
            today.replace(day=1), today
        )
        serialized = jsonable(result)
        return serialized if isinstance(serialized, dict) else None

    async def current_financial_evaluation(self) -> dict[str, Any]:
        """Read current cash and budget health after a completed bank import."""

        await self.start()
        if self.finance is None:
            raise RuntimeError("Finance runtime is not started")
        today = date.today()
        report = await self.finance.daily_monitoring.run(today)
        analysis = report.analysis
        material_projections = [
            {
                "subcategory": item.subcategory,
                "amount": str(item.projected_spend - item.budget),
            }
            for item in analysis.categories
            if item.projection_band is not None and item.projected_spend > item.budget
        ]
        actual_overspends = [
            {
                "subcategory": item.subcategory,
                "amount": str(item.actual_overspend),
            }
            for item in analysis.categories
            if item.actual_overspend > Decimal("0")
        ]
        return {
            "account_outlook": await self.current_account_outlook(),
            "budget": {
                "status": analysis.status.value,
                "total_budget": str(analysis.total_budget),
                "actual_variable_spend": str(analysis.actual_variable_spend),
                "remaining_variable_reserve": (
                    str(analysis.remaining_variable_reserve_before_adjustment)
                    if analysis.remaining_variable_reserve_before_adjustment is not None
                    else None
                ),
                "actual_overspends": actual_overspends,
                "material_projections": material_projections,
            },
        }

    async def start_scheduler(
        self, send_message: Callable[[str, str], Awaitable[None]]
    ) -> None:
        """Start the durable cron scanner after Telegram is ready."""

        await self.start()
        if self._scheduler_task is not None:
            return

        async def execute(task: RecurringTask) -> None:
            chat_id = _chat_id_from_thread(task.owner_thread_id)
            if chat_id is None:
                return
            try:
                result = await self.finance_turn(chat_id, task.prompt, [])
                approval = result.get("approval_request")
                if approval is not None:
                    text = "Scheduled task requires approval before it can continue."
                else:
                    text = str(
                        result.get("output")
                        or "Scheduled task completed with no message."
                    )
            except Exception as exc:
                text = f"Scheduled task failed: {type(exc).__name__}. Please try it manually."
            await send_message(chat_id, text)

        self._scheduler_task = asyncio.create_task(
            scheduler_loop(self.assistant_store, execute),
            name="bruno-recurring-tasks",
        )

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


def _chat_id_from_thread(thread_id: str) -> str | None:
    prefix = "telegram:"
    suffix = ":finance"
    if not thread_id.startswith(prefix) or not thread_id.endswith(suffix):
        return None
    return thread_id[len(prefix) : -len(suffix)] or None
