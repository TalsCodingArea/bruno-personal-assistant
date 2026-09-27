"""Fixed examples for the pure daily budget-monitoring layer."""

from datetime import date
from decimal import Decimal

import pytest
from financial_agent.domain.models import Budget, ProgressiveMode, Transaction
from financial_agent.domain.monitoring import (
    AnalysisStatus,
    FundingSourceKind,
    MonitoringPolicy,
    ObservationKind,
)
from financial_agent.services.daily_budget_analysis import analyze_daily_budget_state

MONTH = date(2026, 8, 1)
AS_OF = date(2026, 8, 20)


def budget(
    subcategory: str,
    amount: str,
    *,
    progressive: ProgressiveMode | None = ProgressiveMode.ACCUMULATED,
    volatility: str | None = "0",
) -> Budget:
    return Budget(
        id=f"budget-{subcategory}",
        subcategory=subcategory,
        month=MONTH,
        amount=Decimal(amount),
        progressive=progressive,
        volatility_percent=Decimal(volatility) if volatility is not None else None,
    )


def expense(
    id_: str,
    amount: str,
    subcategory: str | None,
    *,
    day: int = 20,
    subcategory_options: tuple[str, ...] = (),
) -> Transaction:
    return Transaction(
        id=id_,
        description=id_,
        occurred_on=date(2026, 8, day),
        final_amount=Decimal(amount),
        category="Test" if subcategory is not None else None,
        subcategory=subcategory,
        subcategory_options=subcategory_options,
    )


def test_projection_uses_calendar_pacing_only_for_accumulated_budgets() -> None:
    result = analyze_daily_budget_state(
        [expense("groceries", "260", "Groceries")],
        [
            budget("Groceries", "310"),
            budget(
                "Insurance",
                "310",
                progressive=ProgressiveMode.DISCRETE,
                volatility="100",
            ),
        ],
        AS_OF,
        Decimal("1000"),
        policy=MonitoringPolicy(protected_subcategories=()),
    )

    groceries = next(item for item in result.categories if item.subcategory == "Groceries")
    insurance = next(item for item in result.categories if item.subcategory == "Insurance")
    assert groceries.projected_spend == Decimal("403.00")
    assert groceries.projection_deviation_percent == Decimal("30.00")
    assert groceries.projection_band == Decimal("25")
    assert insurance.projected_spend == Decimal("310.00")
    assert insurance.projection_deviation_percent == Decimal("0.00")
    assert insurance.projection_band is None
    assert [item.kind for item in result.observations] == [
        ObservationKind.PROJECTION_DEVIATION
    ]


def test_material_accumulated_projection_is_immediately_rebudgeted() -> None:
    result = analyze_daily_budget_state(
        [expense("groceries", "260", "Groceries")],
        [budget("Groceries", "310")],
        AS_OF,
        Decimal("1000"),
        policy=MonitoringPolicy(protected_subcategories=()),
    )

    assert result.adjustment_plan is not None
    plan = result.adjustment_plan
    assert plan.budget_changes[0].subcategory == "Groceries"
    assert plan.budget_changes[0].budget_after == Decimal("403.00")
    assert plan.remaining_variable_reserve_after_adjustment == Decimal("597.00")


def test_discrete_budget_is_not_rebudgeted_from_calendar_pacing() -> None:
    result = analyze_daily_budget_state(
        [expense("insurance", "260", "Insurance")],
        [
            budget(
                "Insurance",
                "310",
                progressive=ProgressiveMode.DISCRETE,
                volatility="100",
            )
        ],
        AS_OF,
        Decimal("1000"),
        policy=MonitoringPolicy(protected_subcategories=()),
    )

    assert result.adjustment_plan is not None
    assert result.adjustment_plan.budget_changes == ()
    assert result.observations == ()


def test_uncategorized_and_unbudgeted_expenses_consume_variable_reserve() -> None:
    ambiguous = expense(
        "mixed",
        "20",
        None,
        subcategory_options=("Groceries", "Household"),
    )
    result = analyze_daily_budget_state(
        [
            expense("uncategorized", "50", None),
            expense("coffee", "30", "Coffee"),
            ambiguous,
        ],
        [budget("Groceries", "800")],
        AS_OF,
        Decimal("1000"),
    )

    assert result.starting_variable_reserve == Decimal("200.00")
    assert result.actual_variable_spend == Decimal("100.00")
    assert result.uncategorized_spend == Decimal("70.00")
    assert result.remaining_variable_reserve_before_adjustment == Decimal("100.00")


def test_actual_overspend_uses_positive_variable_reserve_first() -> None:
    result = analyze_daily_budget_state(
        [expense("groceries", "130", "Groceries")],
        [budget("Groceries", "100")],
        AS_OF,
        Decimal("300"),
    )

    assert result.adjustment_plan is not None
    plan = result.adjustment_plan
    assert plan.transfers[0].source_kind is FundingSourceKind.VARIABLE_RESERVE
    assert plan.transfers[0].amount == Decimal("30.00")
    assert plan.budget_changes[0].budget_after == Decimal("130.00")
    assert plan.remaining_variable_reserve_after_adjustment == Decimal("170.00")
    assert result.alerts[0].actual_overspend == Decimal("30.00")
    assert result.alerts[0].adjustment_amount == Decimal("30.00")
    assert result.alerts[0].unresolved_amount == Decimal("0.00")


def test_variable_deficit_is_covered_before_actual_category_overspend() -> None:
    result = analyze_daily_budget_state(
        [
            expense("uncategorized", "120", None),
            expense("groceries", "380", "Groceries"),
            expense("entertainment", "100", "Entertainment"),
        ],
        [
            budget("Groceries", "300"),
            budget("Entertainment", "400", volatility="50"),
            budget(
                "Savings",
                "200",
                progressive=ProgressiveMode.DISCRETE,
                volatility="100",
            ),
        ],
        AS_OF,
        Decimal("1000"),
    )

    assert result.adjustment_plan is not None
    plan = result.adjustment_plan
    assert plan.starting_variable_reserve == Decimal("100.00")
    assert plan.remaining_variable_reserve_before_adjustment == Decimal("-20.00")
    assert plan.unresolved_variable_deficit == Decimal("0.00")
    assert plan.unresolved_budget_shortfall == Decimal("0.00")
    transfers = [
        (item.source_subcategory, item.target_subcategory, item.amount)
        for item in plan.transfers
    ]
    assert transfers == [
        ("Entertainment", None, Decimal("20.00")),
        ("Entertainment", "Groceries", Decimal("80.00")),
    ]
    changes = {item.subcategory: item for item in plan.budget_changes}
    assert changes["Entertainment"].budget_after == Decimal("300.00")
    assert changes["Groceries"].budget_after == Decimal("380.00")
    assert "Savings" not in changes
    assert plan.total_budget_after == Decimal("880.00")
    assert plan.remaining_variable_reserve_after_adjustment == Decimal("0.00")


def test_protected_targets_are_funded_first_and_unfunded_amount_is_explicit() -> None:
    result = analyze_daily_budget_state(
        [
            expense("rent", "1100", "Rent"),
            expense("hobby", "150", "Hobby"),
            expense("takeout", "0", "Takeout"),
        ],
        [
            budget("Rent", "1000"),
            budget("Hobby", "100"),
            budget("Takeout", "116.25", volatility="100"),
        ],
        AS_OF,
        Decimal("1216.25"),
        policy=MonitoringPolicy(
            protected_subcategories=("Rent",),
            preferred_donor_subcategories=("Takeout",),
        ),
    )

    assert result.adjustment_plan is not None
    resolutions = result.adjustment_plan.overspend_resolutions
    assert [item.subcategory for item in resolutions] == ["Rent", "Hobby"]
    assert resolutions[0].unresolved == Decimal("0.00")
    assert resolutions[1].funded_from_budgets == Decimal("16.25")
    assert resolutions[1].unresolved == Decimal("33.75")


def test_missing_income_keeps_analysis_but_blocks_automatic_plan() -> None:
    result = analyze_daily_budget_state(
        [expense("groceries", "130", "Groceries")],
        [budget("Groceries", "100")],
        AS_OF,
        None,
    )

    assert result.status is AnalysisStatus.INCOME_MISSING
    assert result.adjustment_plan is None
    assert result.categories[0].actual_overspend == Decimal("30.00")
    assert result.alerts[0].adjustment_amount == Decimal("0.00")
    assert result.alerts[0].unresolved_amount == Decimal("30.00")
    assert result.observations[-1].kind is ObservationKind.INCOME_MISSING


def test_duplicate_budget_subcategory_is_rejected() -> None:
    duplicate = [budget("Groceries", "100"), budget("Groceries", "200")]

    with pytest.raises(ValueError, match="Duplicate budget"):
        analyze_daily_budget_state([], duplicate, AS_OF, Decimal("1000"))


def test_future_transactions_are_not_included_in_a_daily_run() -> None:
    result = analyze_daily_budget_state(
        [expense("future", "100", None, day=25)],
        [],
        AS_OF,
        Decimal("1000"),
    )

    assert result.actual_variable_spend == Decimal("0.00")
