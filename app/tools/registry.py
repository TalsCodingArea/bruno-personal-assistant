"""Single registration point for the graph's read and write tool catalogs."""

from dataclasses import dataclass

from langchain_core.tools import BaseTool

from app.services.budget_planning import BudgetPlanningService
from app.services.finance_queries import FinanceQueryService
from app.services.interaction import InteractionProfileService
from app.services.ports import ExpenseMonitorLedger
from app.services.profile import FinancialProfileService
from app.tools.draft import (
    build_budget_draft_tools,
    build_draft_tools,
    build_interaction_draft_tools,
    build_profile_draft_tools,
)
from app.tools.read import (
    build_budget_read_tools,
    build_interaction_read_tools,
    build_monitoring_read_tools,
    build_profile_read_tools,
    build_read_tools,
)
from app.tools.write import (
    build_budget_write_tools,
    build_interaction_write_tools,
    build_profile_write_tools,
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
        """All tools; every member of write interrupts before its side effect."""

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
) -> ToolCatalog:
    """Construct all finance tools from one dependency-injected service."""

    return ToolCatalog(
        financial_data=(
            (
                tuple(build_monitoring_read_tools(monitoring_ledger))
                if monitoring_ledger is not None
                else ()
            )
            + (
                tuple(build_budget_read_tools(budget_planning))
                if budget_planning is not None
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
            (
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
        ),
    )
