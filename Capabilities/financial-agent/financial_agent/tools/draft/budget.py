"""Proposal-only monthly budget planning tool."""

from langchain_core.tools import BaseTool, tool

from financial_agent.domain.budget_planning import ExistingTargetBudgetPagesError
from financial_agent.services.budget_planning import BudgetPlanningService
from financial_agent.tools.budget_input import (
    BudgetPageDraftInput,
    MonthlyBudgetPlanInput,
    existing_budget_pages_result,
    prepare_budget_plan,
)
from financial_agent.tools.serialization import JsonValue, jsonable


def build_budget_draft_tools(service: BudgetPlanningService) -> list[BaseTool]:
    @tool("draft_monthly_budget_plan", args_schema=MonthlyBudgetPlanInput)
    async def draft_monthly_budget_plan(
        month: str,
        source_fingerprint: str,
        financial_cap: str,
        cap_basis: str,
        cap_rationale: str,
        items: tuple[BudgetPageDraftInput, ...],
        income_assumption: str | None = None,
        future_expense_shortfall_rationale: str | None = None,
    ) -> JsonValue:
        """Validate proposed Budget pages, cap, reserve, stability, and future funding."""

        try:
            draft = await prepare_budget_plan(
                service,
                month=month,
                source_fingerprint=source_fingerprint,
                financial_cap=financial_cap,
                cap_basis=cap_basis,
                cap_rationale=cap_rationale,
                items=items,
                income_assumption=income_assumption,
                future_expense_shortfall_rationale=future_expense_shortfall_rationale,
            )
        except ExistingTargetBudgetPagesError as error:
            return existing_budget_pages_result(error, month=month)
        return jsonable(draft)

    return [draft_monthly_budget_plan]
