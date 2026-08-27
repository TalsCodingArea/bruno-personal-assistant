"""Composition root: connect concrete integrations to application layers."""

from dataclasses import dataclass
from datetime import date

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.config import Settings, get_settings
from app.graphs.conversation import ConversationGraph, build_conversation_graph
from app.graphs.daily_budget import DailyBudgetGraph, build_daily_budget_graph
from app.graphs.expense_monitor import ExpenseMonitorGraph, build_expense_monitor_graph
from app.integrations.expense_monitor_ledger import SQLiteExpenseMonitorLedger
from app.integrations.expense_notifications import TraceExpenseAlertNotifier
from app.integrations.notion import NotionClient
from app.integrations.notion_budget_mutation import NotionBudgetMutationRepository
from app.integrations.notion_finance import NotionFinanceReader
from app.integrations.notion_operational_context import (
    NotionOperationalContextRepository,
)
from app.integrations.notion_profile import NotionFinancialProfileRepository
from app.integrations.notion_schema import FinanceDataSources
from app.integrations.openai_budget_review import (
    BlockingBudgetPreferenceReviewer,
    OpenAIBudgetPreferenceReviewer,
)
from app.services.budget_mutation import BudgetMutationService
from app.services.daily_budget_monitoring import DailyBudgetMonitoringService
from app.services.expense_monitor_workflow import ExpenseMonitorWorkflowService
from app.services.finance_queries import FinanceQueryService
from app.services.interaction import InteractionProfileService
from app.services.monitoring_inputs import DailyMonitoringInputService
from app.services.operational_context import OperationalContextService
from app.services.profile import FinancialProfileService
from app.tools.registry import ToolCatalog, build_tool_catalog


@dataclass(slots=True)
class FinanceApplication:
    """Live application dependencies and their owned Notion connection."""

    notion: NotionClient
    finance: FinanceQueryService
    monitoring_inputs: DailyMonitoringInputService
    daily_monitoring: DailyBudgetMonitoringService
    operational_context: OperationalContextService
    budget_mutation: BudgetMutationService
    daily_budget_graph: DailyBudgetGraph
    expense_monitoring: ExpenseMonitorWorkflowService
    expense_monitor_graph: ExpenseMonitorGraph
    profile: FinancialProfileService
    interaction: InteractionProfileService
    tools: ToolCatalog

    async def aclose(self) -> None:
        await self.notion.aclose()

    async def __aenter__(self) -> "FinanceApplication":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


@dataclass(slots=True)
class ConversationApplication:
    """Read-only graph plus the dependencies whose lifetime it uses."""

    dependencies: FinanceApplication
    graph: ConversationGraph

    async def aclose(self) -> None:
        await self.dependencies.aclose()

    async def __aenter__(self) -> "ConversationApplication":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


def build_finance_application(
    settings: Settings | None = None,
    *,
    mutation_review_model: BaseChatModel | None = None,
) -> FinanceApplication:
    """Connect all finance tools to the real async ``notion-client`` SDK."""

    settings = settings or get_settings()
    if settings.notion_token is None:
        raise ValueError("FINANCE_AGENT_NOTION_TOKEN is required")
    source_ids = settings.require_notion_data_source_ids()
    sources = FinanceDataSources(**source_ids)
    notion = NotionClient(
        settings.notion_token.get_secret_value(),
        api_version=settings.notion_api_version,
    )
    finance_reader = NotionFinanceReader(notion, sources=sources)
    finance = FinanceQueryService(finance_reader)
    profile = FinancialProfileService(
        NotionFinancialProfileRepository(notion, sources.financial_rules)
    )
    monitoring_inputs = DailyMonitoringInputService(finance_reader, profile)
    daily_monitoring = DailyBudgetMonitoringService(monitoring_inputs)
    operational_context = OperationalContextService(
        NotionOperationalContextRepository(notion, sources.financial_rules)
    )
    preference_reviewer = (
        OpenAIBudgetPreferenceReviewer(mutation_review_model)
        if mutation_review_model is not None
        else BlockingBudgetPreferenceReviewer()
    )
    budget_mutation = BudgetMutationService(
        daily_monitoring,
        NotionBudgetMutationRepository(notion, sources.budgets),
        preference_reviewer,
    )
    daily_budget_graph = build_daily_budget_graph(
        daily_monitoring,
        operational_context,
        budget_mutation,
    )
    expense_monitor_ledger = SQLiteExpenseMonitorLedger(
        settings.expense_monitor_ledger_path
    )
    expense_monitoring = ExpenseMonitorWorkflowService(
        finance_reader,
        monitoring_inputs,
        expense_monitor_ledger,
        TraceExpenseAlertNotifier(),
        mode=settings.expense_monitor_mode,
    )
    expense_monitor_graph = build_expense_monitor_graph(expense_monitoring)
    interaction = InteractionProfileService(profile)
    return FinanceApplication(
        notion=notion,
        finance=finance,
        monitoring_inputs=monitoring_inputs,
        daily_monitoring=daily_monitoring,
        operational_context=operational_context,
        budget_mutation=budget_mutation,
        daily_budget_graph=daily_budget_graph,
        expense_monitoring=expense_monitoring,
        expense_monitor_graph=expense_monitor_graph,
        profile=profile,
        interaction=interaction,
        tools=build_tool_catalog(
            finance,
            profile,
            interaction,
            expense_monitor_ledger,
        ),
    )


def build_conversation_application(
    model: BaseChatModel,
    checkpointer: BaseCheckpointSaver[str],
    *,
    today: date,
    settings: Settings | None = None,
) -> ConversationApplication:
    """Build the first graph with the real Notion-backed safe tool catalog."""

    resolved_settings = settings or get_settings()
    dependencies = build_finance_application(
        resolved_settings,
        mutation_review_model=model,
    )
    graph = build_conversation_graph(
        model,
        dependencies.tools.approval_conversation,
        checkpointer,
        today=today,
        currency=resolved_settings.currency,
        profile_service=dependencies.profile,
    )
    return ConversationApplication(dependencies=dependencies, graph=graph)
