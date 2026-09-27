"""Fixed examples for the pure finance layer."""

from datetime import date
from decimal import Decimal

import pytest
from financial_agent.domain.models import Budget, PlannedExpense, ProgressiveMode, Transaction
from financial_agent.domain.money import money
from financial_agent.services.calculations import (
    budget_status,
    draft_category_updates,
    draft_planned_expense,
    month_end_forecast,
    monthly_category_summary,
    planned_expense_monthly_allocation,
    suggest_categories,
    uncategorized_review,
    variable_spending_pool,
)


def transaction(
    id_: str,
    description: str,
    day: int,
    amount: str,
    category: str | None,
    subcategory: str | None,
) -> Transaction:
    return Transaction(
        id=id_,
        description=description,
        occurred_on=date(2026, 8, day),
        final_amount=Decimal(amount),
        category=category,
        subcategory=subcategory,
    )


SAMPLE_TRANSACTIONS = [
    transaction("1", "Neighborhood Market", 2, "50.005", "Food", "Groceries"),
    transaction("2", "Neighborhood Market", 8, "25.50", "Food", "Groceries"),
    transaction("3", "Cafe", 10, "12.00", "Food", "Coffee"),
    transaction("4", "Mystery Shop", 12, "7.25", None, None),
]

SAMPLE_BUDGETS = [
    Budget(
        "b1",
        "Groceries",
        date(2026, 8, 1),
        Decimal("100"),
        progressive=ProgressiveMode.ACCUMULATED,
        volatility_percent=Decimal("80"),
    ),
    Budget(
        "b2",
        "Coffee",
        date(2026, 8, 1),
        Decimal("20"),
        progressive=ProgressiveMode.DISCRETE,
        volatility_percent=Decimal("0"),
    ),
]


def test_money_rejects_float_and_rounds_half_up() -> None:
    assert money("10.005") == Decimal("10.01")
    with pytest.raises(TypeError):
        money(10.01)  # type: ignore[arg-type]


def test_monthly_category_summary_uses_decimal_and_fixed_month() -> None:
    summary = monthly_category_summary(
        SAMPLE_TRANSACTIONS, date(2026, 8, 19), budgets=SAMPLE_BUDGETS
    )

    assert summary.month == date(2026, 8, 1)
    assert summary.total == Decimal("94.76")
    assert summary.categories[0].subcategory == "Groceries"
    assert summary.categories[0].amount == Decimal("75.51")
    assert summary.categories[0].transaction_count == 2
    assert summary.categories[0].expense_class == "regular"
    assert summary.categories[-1].expense_class == "variable"


def test_uncategorized_review_requires_both_category_levels() -> None:
    result = uncategorized_review(SAMPLE_TRANSACTIONS)

    assert [item.id for item in result] == ["4"]


def test_multiple_subcategories_are_preserved_and_flagged_for_review() -> None:
    ambiguous = Transaction(
        id="multi",
        description="Mixed purchase",
        occurred_on=date(2026, 8, 15),
        final_amount=Decimal("100"),
        category_options=("Food", "Home"),
        subcategory_options=("Groceries", "Household supplies"),
    )

    review = uncategorized_review([ambiguous])
    summary = monthly_category_summary([ambiguous], date(2026, 8, 1))
    status = budget_status([ambiguous], [], date(2026, 8, 1))

    assert review == (ambiguous,)
    assert summary.categories[0].category == "Multiple: Food | Home"
    assert summary.categories[0].subcategory == "Multiple: Groceries | Household supplies"
    assert status.variable_categories[0].subcategory == (
        "Multiple: Groceries | Household supplies"
    )


def test_variable_spending_pool_reports_shortfall_without_negative_pool() -> None:
    result = variable_spending_pool(
        Decimal("5000"), Decimal("3500"), Decimal("1000"), Decimal("750")
    )

    assert result.available == Decimal("0.00")
    assert result.shortfall == Decimal("250.00")


def test_planned_expense_starts_at_most_three_contribution_months_early() -> None:
    planned = PlannedExpense(
        id="trip",
        name="Trip",
        target_amount=Decimal("900"),
        due_date=date(2026, 11, 15),
    )

    result = planned_expense_monthly_allocation(planned, date(2026, 8, 21))

    assert result.status == "scheduled"
    assert result.saving_starts_on == date(2026, 9, 1)
    assert result.contribution_months == 3
    assert result.monthly_allocation == Decimal("300.00")


def test_planned_expense_uses_remaining_near_due_date() -> None:
    planned = PlannedExpense(
        id="insurance",
        name="Insurance",
        target_amount=Decimal("1000"),
        saved_amount=Decimal("400"),
        due_date=date(2026, 9, 20),
    )

    result = planned_expense_monthly_allocation(planned, date(2026, 8, 21))

    assert result.contribution_months == 2
    assert result.monthly_allocation == Decimal("300.00")


def test_month_end_forecast_projects_observed_daily_pace() -> None:
    result = month_end_forecast(
        SAMPLE_TRANSACTIONS,
        SAMPLE_BUDGETS,
        date(2026, 8, 1),
        date(2026, 8, 15),
        Decimal("5000"),
        planned_allocations=Decimal("300"),
        buffer=Decimal("200"),
    )

    assert result.spent_to_date == Decimal("94.76")
    assert result.regular_spent_to_date == Decimal("87.51")
    assert result.variable_spent_to_date == Decimal("7.25")
    assert result.accumulated_regular_projection == Decimal("156.05")
    assert result.discrete_regular_projection == Decimal("20.00")
    assert result.variable_projection == Decimal("14.98")
    assert result.projected_expenses == Decimal("191.03")
    assert result.projected_available == Decimal("4308.97")


def test_budget_status_matches_exact_subcategory() -> None:
    result = budget_status(SAMPLE_TRANSACTIONS, SAMPLE_BUDGETS, date(2026, 8, 1))

    groceries = next(item for item in result.categories if item.subcategory == "Groceries")
    assert groceries.spent == Decimal("75.51")
    assert groceries.remaining == Decimal("24.49")
    assert groceries.progressive is ProgressiveMode.ACCUMULATED
    assert groceries.volatility_percent == Decimal("80.00")
    assert groceries.controllable_remaining == Decimal("19.59")
    assert result.regular_spend == Decimal("87.51")
    assert result.variable_spend == Decimal("7.25")
    assert result.variable_categories[0].subcategory == "Uncategorized"
    assert result.variable_categories[0].transaction_count == 1


def test_budget_status_rejects_duplicate_exact_subcategory() -> None:
    duplicates = [
        Budget("b1", "Groceries", date(2026, 8, 1), Decimal("100")),
        Budget("b2", "Groceries", date(2026, 8, 1), Decimal("150")),
    ]

    with pytest.raises(ValueError, match="Duplicate budget"):
        budget_status(SAMPLE_TRANSACTIONS, duplicates, date(2026, 8, 1))


def test_category_suggestions_are_grounded_in_matching_history() -> None:
    unknown = [transaction("new", "Neighborhood Market", 20, "30", None, None)]
    suggestions = suggest_categories(unknown, SAMPLE_TRANSACTIONS)
    drafts = draft_category_updates(suggestions)

    assert len(suggestions) == 1
    assert suggestions[0].subcategory == "Groceries"
    assert suggestions[0].confidence == Decimal("1.00")
    assert drafts[0].transaction_id == "new"


def test_draft_planned_expense_has_no_persistence_and_contains_allocation() -> None:
    result = draft_planned_expense(
        "Laptop",
        Decimal("1200"),
        date(2026, 12, 10),
        date(2026, 8, 21),
    )

    assert result.monthly_allocation == Decimal("400.00")
    assert result.saving_starts_on == date(2026, 10, 1)
