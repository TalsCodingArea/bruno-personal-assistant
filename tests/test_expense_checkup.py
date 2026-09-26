"""Post-expense check-up result semantics."""

from datetime import date
from decimal import Decimal

from financial_agent.domain.daily_budget_workflow import (
    DailyBudgetMutationDisposition,
    DailyBudgetMutationOutcome,
)
from financial_agent.domain.models import Budget, Income, ProgressiveMode, Transaction
from financial_agent.domain.monitoring import (
    DailyBudgetMonitoringReport,
    MonitoringInputSnapshot,
    MonitoringPolicy,
)
from financial_agent.services.daily_budget_analysis import analyze_daily_budget_state
from financial_agent.tools.automation.monitoring import expense_checkup_result


def test_unfunded_budget_shortfall_becomes_savings_requirement() -> None:
    as_of = date(2026, 8, 31)
    transactions = (
        Transaction(
            "expense",
            "Market",
            as_of,
            Decimal("120"),
            category="Food",
            subcategory="Groceries",
        ),
    )
    budgets = (
        Budget(
            "groceries",
            "Groceries",
            date(2026, 8, 1),
            Decimal("100"),
            ProgressiveMode.ACCUMULATED,
            Decimal("0"),
        ),
    )
    policy = MonitoringPolicy(automatic_budget_adjustments_enabled=True)
    income = Decimal("100")
    report = DailyBudgetMonitoringReport(
        inputs=MonitoringInputSnapshot(
            month=date(2026, 8, 1),
            as_of=as_of,
            transactions=transactions,
            incomes=(Income("salary", "Salary", as_of, income),),
            budgets=budgets,
            monthly_income=income,
            policy=policy,
            policy_sources=(),
        ),
        analysis=analyze_daily_budget_state(
            transactions,
            budgets,
            as_of,
            income,
            policy=policy,
        ),
    )
    result = expense_checkup_result(
        {
            "report": report,
            "mutation_outcome": DailyBudgetMutationOutcome(
                DailyBudgetMutationDisposition.DISABLED,
                "No fundable budget change remains",
            ),
        }
    )

    assert result["unresolved_budget_shortfall"] == "20.00"
    assert result["effective_remaining_variable_after"] == "-20.00"
    assert result["savings_required"] is True
    assert result["savings_amount"] == "20.00"
