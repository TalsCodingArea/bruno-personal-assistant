"""Approval-interrupted creation of validated monthly Budget pages."""

from typing import Any

from langchain_core.tools import BaseTool, tool
from langgraph.types import interrupt

from app.services.budget_planning import BudgetPlanningService
from app.tools.budget_input import (
    BudgetPageDraftInput,
    MonthlyBudgetPlanInput,
    prepare_budget_plan,
)
from app.tools.serialization import JsonValue, jsonable
from app.tools.write.approval import is_approved


def build_budget_write_tools(service: BudgetPlanningService) -> list[BaseTool]:
    @tool("apply_monthly_budget_plan", args_schema=MonthlyBudgetPlanInput)
    async def apply_monthly_budget_plan(
        month: str,
        source_fingerprint: str,
        financial_cap: str,
        cap_basis: str,
        cap_rationale: str,
        items: tuple[BudgetPageDraftInput, ...],
        income_assumption: str | None = None,
        future_expense_shortfall_rationale: str | None = None,
    ) -> JsonValue:
        """Rebuild the draft, pause for approval, then create its Budget pages."""

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
        decision: Any = interrupt(
            {
                "type": "monthly_budget_plan_approval",
                "allowed_actions": ["approve", "reject"],
                "message": "Approve creating these monthly Budget pages in Notion?",
                "proposal": jsonable(draft),
            }
        )
        if not is_approved(decision):
            return {
                "status": "rejected",
                "message": "The proposed monthly Budget pages were not created.",
            }
        result = await service.apply_plan(draft)
        return {"status": "applied", "result": jsonable(result)}

    return [apply_monthly_budget_plan]
