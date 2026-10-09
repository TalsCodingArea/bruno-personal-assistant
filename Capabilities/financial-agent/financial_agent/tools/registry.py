"""Single registration point for the graph's read and write tool catalogs."""

from dataclasses import dataclass

from langchain_core.tools import BaseTool

from financial_agent.services.budget_planning import BudgetPlanningService
from financial_agent.services.cashflow import CashflowService
from financial_agent.services.category_correction import CategoryCorrectionService
from financial_agent.services.finance_queries import FinanceQueryService
from financial_agent.services.interaction import InteractionProfileService
from financial_agent.services.ports import ExpenseMonitorLedger, RecurringTaskManager
from financial_agent.services.profile import FinancialProfileService
from financial_agent.tools.category_correction import build_category_correction_tools
from financial_agent.tools.draft import (
    build_budget_draft_tools,
    build_draft_tools,
    build_interaction_draft_tools,
    build_profile_draft_tools,
)
from financial_agent.tools.read import (
    build_budget_read_tools,
    build_cashflow_read_tools,
    build_interaction_read_tools,
    build_monitoring_read_tools,
    build_profile_read_tools,
    build_read_tools,
    build_recurring_read_tools,
)
from financial_agent.tools.write import (
    build_budget_write_tools,
    build_interaction_write_tools,
    build_profile_write_tools,
    build_recurring_write_tools,
)


@dataclass(frozen=True, slots=True)
class ToolCatalog:
    """Tool groups mirror finance responsibilities and side-effect policy."""

    financial_data: tuple[BaseTool, ...]
    calculations: tuple[BaseTool, ...]
    preference_read: tuple[BaseTool, ...]
    draft: tuple[BaseTool, ...]
    write: tuple[BaseTool, ...]

    @property
    def read(self) -> tuple[BaseTool, ...]:
        return self.financial_data + self.calculations + self.preference_read

    @property
    def conversation(self) -> tuple[BaseTool, ...]:
        """Tools that can finish without human approval."""

        return self.read + self.draft

    @property
    def approval_conversation(self) -> tuple[BaseTool, ...]:
        """All tools; each mutation enforces its own approval or autonomy policy."""

        return self.conversation + self.write

    @property
    def all(self) -> tuple[BaseTool, ...]:
        """Every registered tool, including approval-interrupted mutations."""

        return self.approval_conversation


def build_tool_catalog(
    service: FinanceQueryService,
    profile: FinancialProfileService | None = None,
    interaction: InteractionProfileService | None = None,
    monitoring_ledger: ExpenseMonitorLedger | None = None,
    budget_planning: BudgetPlanningService | None = None,
    cashflow: CashflowService | None = None,
    recurring_tasks: RecurringTaskManager | None = None,
    category_corrections: CategoryCorrectionService | None = None,
) -> ToolCatalog:
    """Construct all finance tools from one dependency-injected service."""

    correction_tools = (
        tuple(build_category_correction_tools(category_corrections))
        if category_corrections is not None
        else ()
    )
    return ToolCatalog(
        financial_data=(
            correction_tools[:1]
            + (
                tuple(build_monitoring_read_tools(monitoring_ledger))
                if monitoring_ledger is not None
                else ()
            )
            + (
                tuple(build_budget_read_tools(budget_planning))
                if budget_planning is not None
                else ()
            )
            + (
                tuple(build_cashflow_read_tools(cashflow))
                if cashflow is not None
                else ()
            )
            + (
                tuple(build_recurring_read_tools(recurring_tasks))
                if recurring_tasks is not None
                else ()
            )
        ),
        calculations=tuple(build_read_tools(service)),
        preference_read=(
            (tuple(build_profile_read_tools(profile)) if profile is not None else ())
            + (
                tuple(build_interaction_read_tools(interaction))
                if interaction is not None
                else ()
            )
        ),
        draft=(
            tuple(build_draft_tools(service))
            + (
                tuple(build_budget_draft_tools(budget_planning))
                if budget_planning is not None
                else ()
            )
            + (tuple(build_profile_draft_tools(profile)) if profile is not None else ())
            + (
                tuple(build_interaction_draft_tools(interaction))
                if interaction is not None
                else ()
            )
        ),
        write=(
            correction_tools[1:]
            + (
                tuple(build_budget_write_tools(budget_planning))
                if budget_planning is not None
                else ()
            )
            + (tuple(build_profile_write_tools(profile)) if profile is not None else ())
            + (
                tuple(build_interaction_write_tools(interaction))
                if interaction is not None
                else ()
            )
            + (
                tuple(build_recurring_write_tools(recurring_tasks))
                if recurring_tasks is not None
                else ()
            )
        ),
    )
