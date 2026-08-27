"""Fixed examples for expense diffs, impact, severity, and response drafts."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.domain.expense_monitoring import (
    AlertState,
    ExpenseChangedEvent,
    ExpenseEventType,
    ExpenseSeverity,
)
from app.domain.models import Budget, Income, ProgressiveMode, Transaction
from app.domain.monitoring import MonitoringInputSnapshot, MonitoringPolicy
from app.services.expense_monitoring import (
    calculate_expense_impact,
    classify_expense_impact,
    draft_budget_reallocation,
    evaluate_expense_alert,
    resolve_expense_diff,
    snapshot_transaction,
)

MONTH = date(2026, 8, 1)
AS_OF = date(2026, 8, 20)
OBSERVED_AT = datetime(2026, 8, 20, 12, tzinfo=UTC)


def event(event_id: str = "event-1", kind: str = "updated") -> ExpenseChangedEvent:
    return ExpenseChangedEvent(
        event_id,
        "expense-page",
        OBSERVED_AT,
        ExpenseEventType(kind),
    )


def expense(amount: str, subcategory: str | None, *, id_: str = "expense-page") -> Transaction:
    return Transaction(
        id=id_,
        description="Expense",
        occurred_on=AS_OF,
        final_amount=Decimal(amount),
        category="Food" if subcategory is not None else None,
        subcategory=subcategory,
    )


def budget(
    name: str,
    amount: str,
    *,
    progressive: ProgressiveMode = ProgressiveMode.ACCUMULATED,
    volatility: str = "0",
) -> Budget:
    return Budget(
        id=f"budget-{name}",
        subcategory=name,
        month=MONTH,
        amount=Decimal(amount),
        progressive=progressive,
        volatility_percent=Decimal(volatility),
    )


def financial_state(
    transactions: tuple[Transaction, ...],
    budgets: tuple[Budget, ...],
    *,
    income: str = "1200",
    policy: MonitoringPolicy | None = None,
) -> MonitoringInputSnapshot:
    value = Decimal(income)
    return MonitoringInputSnapshot(
        month=MONTH,
        as_of=AS_OF,
        transactions=transactions,
        incomes=(Income("income", "Salary", MONTH, value),),
        budgets=budgets,
        monthly_income=value,
        policy=policy or MonitoringPolicy(protected_subcategories=()),
        policy_sources=(),
    )


def test_edited_classification_removes_old_impact_and_adds_new_amount() -> None:
    previous = snapshot_transaction(expense("100", "Groceries"), processed_at=OBSERVED_AT)
    current = snapshot_transaction(expense("125", "Takeout"), processed_at=OBSERVED_AT)

    diff = resolve_expense_diff(event(), previous, current)

    assert [(item.scope, item.amount) for item in diff.deltas] == [
        ("Groceries", Decimal("-100.00")),
        ("Takeout", Decimal("125.00")),
    ]
    assert sum(item.amount for item in diff.deltas) == Decimal("25.00")


def test_duplicate_fingerprint_has_no_diff() -> None:
    snapshot = snapshot_transaction(expense("100", "Groceries"), processed_at=OBSERVED_AT)

    diff = resolve_expense_diff(event(), snapshot, snapshot)

    assert diff.changed is False
    assert diff.deltas == ()


def test_accumulated_impact_uses_other_spending_and_calendar_pacing() -> None:
    previous = snapshot_transaction(expense("60", "Takeout"), processed_at=OBSERVED_AT)
    current_transaction = expense("85", "Takeout")
    current = snapshot_transaction(current_transaction, processed_at=OBSERVED_AT)
    other = expense("420", "Takeout", id_="other")
    snapshot = financial_state(
        (current_transaction, other),
        (budget("Takeout", "600", volatility="90"),),
    )

    impact = calculate_expense_impact(
        resolve_expense_diff(event(), previous, current),
        {MONTH: snapshot},
    )

    line = impact.lines[0]
    assert line.spent_before == Decimal("480.00")
    assert line.expense_delta == Decimal("25.00")
    assert line.spent_after == Decimal("505.00")
    assert line.expected_spend_by_today == Decimal("387.10")
    assert line.projected_month_end == Decimal("782.75")
    assert line.budget_variance_projection == Decimal("182.75")


def test_discrete_expense_uses_full_allocation_instead_of_daily_pacing() -> None:
    transaction = expense("310", "Insurance")
    snapshot = financial_state(
        (transaction,),
        (budget("Insurance", "350", progressive=ProgressiveMode.DISCRETE),),
    )
    diff = resolve_expense_diff(
        event(kind="created"),
        None,
        snapshot_transaction(transaction, processed_at=OBSERVED_AT),
    )

    line = calculate_expense_impact(diff, {MONTH: snapshot}).lines[0]
    assessment = classify_expense_impact(
        calculate_expense_impact(diff, {MONTH: snapshot}),
        {MONTH: snapshot},
    )[0]

    assert line.expected_spend_by_today is None
    assert line.projected_month_end == Decimal("350.00")
    assert assessment.severity is ExpenseSeverity.NONE


def test_variable_expense_is_measured_against_remaining_pool() -> None:
    transaction = expense("250", "Coffee")
    snapshot = financial_state(
        (transaction,),
        (budget("Rent", "900"),),
        income="1000",
    )
    diff = resolve_expense_diff(
        event(kind="created"),
        None,
        snapshot_transaction(transaction, processed_at=OBSERVED_AT),
    )
    impact = calculate_expense_impact(diff, {MONTH: snapshot})
    assessment = classify_expense_impact(impact, {MONTH: snapshot})[0]

    assert impact.lines[0].expense_class == "variable"
    assert impact.lines[0].variable_pool_before == Decimal("100.00")
    assert impact.lines[0].variable_pool_after == Decimal("-150.00")
    assert assessment.severity is ExpenseSeverity.CRITICAL


def test_alert_dedupes_inside_cooldown_but_allows_material_increase() -> None:
    transaction = expense("130", "Takeout")
    policy = MonitoringPolicy(
        protected_subcategories=(),
        material_projection_increase=Decimal("50"),
        alert_cooldown_hours=24,
    )
    snapshot = financial_state(
        (transaction,),
        (budget("Takeout", "100"),),
        policy=policy,
    )
    diff = resolve_expense_diff(
        event(kind="created"),
        None,
        snapshot_transaction(transaction, processed_at=OBSERVED_AT),
    )
    assessment = classify_expense_impact(
        calculate_expense_impact(diff, {MONTH: snapshot}), {MONTH: snapshot}
    )[0]
    prior = AlertState(
        month=MONTH,
        scope="Takeout",
        last_severity=ExpenseSeverity.CRITICAL,
        last_projected_variance=Decimal("190"),
        last_evaluated_at=OBSERVED_AT - timedelta(hours=2),
        last_alerted_at=OBSERVED_AT - timedelta(hours=2),
    )

    suppressed = evaluate_expense_alert(assessment, prior, event(), snapshot)

    assert suppressed.alert is None
    assert suppressed.next_state.last_severity is ExpenseSeverity.CRITICAL

    materially_lower_prior = AlertState(
        month=MONTH,
        scope="Takeout",
        last_severity=ExpenseSeverity.CRITICAL,
        last_projected_variance=Decimal("40"),
        last_evaluated_at=OBSERVED_AT - timedelta(hours=2),
        last_alerted_at=OBSERVED_AT - timedelta(hours=2),
    )
    escalated = evaluate_expense_alert(
        assessment, materially_lower_prior, event(), snapshot
    )
    assert escalated.alert is not None
    assert "materially" in escalated.alert.deduplication_reason


def test_reallocation_draft_uses_only_eligible_budget_donor_and_preserves_total() -> None:
    transaction = expense("130", "Groceries")
    policy = MonitoringPolicy(
        protected_subcategories=("Groceries", "Rent"),
        reallocation_enabled=True,
        reallocation_max_amount=Decimal("100"),
    )
    snapshot = financial_state(
        (transaction,),
        (
            budget("Groceries", "100"),
            budget("Entertainment", "400", volatility="50"),
        ),
        income="500",
        policy=policy,
    )
    diff = resolve_expense_diff(
        event(kind="created"),
        None,
        snapshot_transaction(transaction, processed_at=OBSERVED_AT),
    )
    impact = calculate_expense_impact(diff, {MONTH: snapshot})
    assessments = classify_expense_impact(impact, {MONTH: snapshot})

    drafts = draft_budget_reallocation(diff, assessments, {MONTH: snapshot})

    assert len(drafts) == 1
    changes = {item.subcategory: item for item in drafts[0].changes}
    assert changes["Entertainment"].proposed == Decimal("370.00")
    assert changes["Groceries"].proposed == Decimal("130.00")
    assert drafts[0].total_before == drafts[0].total_after
    assert "Rent" not in changes
