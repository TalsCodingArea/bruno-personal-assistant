"""Trusted automation adapter for one post-expense budget check-up."""

from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from langchain_core.tools import BaseTool, tool

from financial_agent.domain.daily_budget_workflow import DailyBudgetMutationOutcome
from financial_agent.domain.monitoring import DailyBudgetMonitoringReport, ObservationKind


class DailyBudgetGraphRunner(Protocol):
    async def ainvoke(self, input: dict[str, str]) -> dict[str, Any]: ...


def build_expense_checkup_tools(graph: DailyBudgetGraphRunner) -> tuple[BaseTool, ...]:
    """Build the unattended check-up tool used after a debounce window."""

    @tool("check_expenses")
    async def check_expenses(as_of: str = "") -> dict[str, object]:
        """Analyze current expenses and run any authorized guarded rebalance."""

        target = date.fromisoformat(as_of) if as_of else date.today()
        state = await graph.ainvoke({"as_of": target.isoformat()})
        return expense_checkup_result(state)

    return (check_expenses,)


def expense_checkup_result(state: dict[str, Any]) -> dict[str, object]:
    """Reduce a graph result to the facts needed by a notification transport."""

    report = state.get("report")
    outcome = state.get("mutation_outcome")
    if not isinstance(report, DailyBudgetMonitoringReport):
        raise TypeError("Daily budget graph returned no monitoring report")
    if not isinstance(outcome, DailyBudgetMutationOutcome):
        raise TypeError("Daily budget graph returned no mutation outcome")

    analysis = report.analysis
    plan = analysis.adjustment_plan
    projections = [
        {
            "subcategory": item.subcategory,
            "amount": str(item.amount),
            "percent": str(item.percent),
            "band": str(item.band),
        }
        for item in analysis.observations
        if item.kind is ObservationKind.PROJECTION_DEVIATION
    ]
    actual_overspends = [
        {
            "subcategory": item.subcategory,
            "actual": str(item.actual_spend),
            "budget": str(item.budget),
            "overspend": str(item.actual_overspend),
        }
        for item in analysis.categories
        if item.actual_overspend > 0
    ]
    observations = [item.kind.value for item in analysis.observations]
    changes = (
        [
            {
                "subcategory": item.subcategory,
                "before": str(item.budget_before),
                "after": str(item.budget_after),
            }
            for item in plan.budget_changes
        ]
        if plan is not None
        else []
    )
    unresolved_variable = (
        str(plan.unresolved_variable_deficit) if plan is not None else None
    )
    unresolved_budget = (
        plan.unresolved_budget_shortfall if plan is not None else Decimal("0")
    )
    effective_remaining = (
        plan.remaining_variable_reserve_after_adjustment - unresolved_budget
        if plan is not None
        else None
    )
    savings_amount = (
        max(-effective_remaining, Decimal("0"))
        if effective_remaining is not None
        else Decimal("0")
    )
    savings_required = savings_amount > 0
    should_notify = bool(
        projections
        or actual_overspends
        or ObservationKind.VARIABLE_RESERVE_DEFICIT.value in observations
        or ObservationKind.BUDGETS_EXCEED_INCOME.value in observations
        or ObservationKind.INCOME_MISSING.value in observations
        or changes
        or outcome.disposition.value not in {"not_needed", "already_applied"}
    )
    return {
        "as_of": analysis.as_of.isoformat(),
        "should_notify": should_notify,
        "status": analysis.status.value,
        "observations": observations,
        "projections": projections,
        "actual_overspends": actual_overspends,
        "monthly_income": (
            str(analysis.monthly_income) if analysis.monthly_income is not None else None
        ),
        "total_budget": str(analysis.total_budget),
        "actual_variable_spend": str(analysis.actual_variable_spend),
        "remaining_variable_before": (
            str(analysis.remaining_variable_reserve_before_adjustment)
            if analysis.remaining_variable_reserve_before_adjustment is not None
            else None
        ),
        "remaining_variable_after": (
            str(plan.remaining_variable_reserve_after_adjustment)
            if plan is not None
            else None
        ),
        "unresolved_variable_deficit": unresolved_variable,
        "unresolved_budget_shortfall": str(unresolved_budget),
        "effective_remaining_variable_after": (
            str(effective_remaining) if effective_remaining is not None else None
        ),
        "savings_required": savings_required,
        "savings_amount": str(savings_amount),
        "budget_changes": changes,
        "mutation": {
            "disposition": outcome.disposition.value,
            "reason": outcome.reason,
        },
    }
