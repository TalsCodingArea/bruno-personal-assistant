"""Fixed scenarios for operational-context reconciliation and persistence."""

import asyncio
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from financial_agent.domain.budget_mutation import (
    AppliedBudgetMutationItem,
    BudgetMutationResult,
    BudgetMutationStatus,
)
from financial_agent.domain.models import Budget, Income, ProgressiveMode, Transaction
from financial_agent.domain.monitoring import (
    DailyBudgetMonitoringReport,
    MonitoringInputSnapshot,
    MonitoringPolicy,
)
from financial_agent.domain.operational_context import (
    OperationalContextChangeKind,
    OperationalContextEntry,
    OperationalContextKind,
    OperationalContextLifecycle,
    OperationalContextState,
    OperationalContextVersionConflict,
)
from financial_agent.services.daily_budget_analysis import analyze_daily_budget_state
from financial_agent.services.operational_context import OperationalContextService
from financial_agent.services.operational_reconciliation import reconcile_daily_report


def report(
    *,
    as_of: date = date(2026, 8, 20),
    groceries_spend: str = "260",
    income: str | None = "1000",
) -> DailyBudgetMonitoringReport:
    transactions = (
        Transaction(
            "expense",
            "Market",
            as_of,
            Decimal(groceries_spend),
            category="Food",
            subcategory="Groceries",
        ),
    )
    incomes = (
        (Income("salary", "Salary", date(2026, 8, 1), Decimal(income)),)
        if income is not None
        else ()
    )
    budgets = (
        Budget(
            "budget",
            "Groceries",
            date(2026, 8, 1),
            Decimal("310"),
            ProgressiveMode.ACCUMULATED,
            Decimal("50"),
        ),
    )
    snapshot = MonitoringInputSnapshot(
        month=date(2026, 8, 1),
        as_of=as_of,
        transactions=transactions,
        incomes=incomes,
        budgets=budgets,
        monthly_income=Decimal(income) if income is not None else None,
        policy=MonitoringPolicy(),
        policy_sources=(),
    )
    analysis = analyze_daily_budget_state(
        transactions,
        budgets,
        as_of,
        snapshot.monthly_income,
        policy=snapshot.policy,
    )
    return DailyBudgetMonitoringReport(inputs=snapshot, analysis=analysis)


def persisted(state: OperationalContextState, page_id: str = "current") -> OperationalContextEntry:
    return OperationalContextEntry(page_id=page_id, state=state)


class InMemoryOperationalContextRepository:
    def __init__(self, entries: tuple[OperationalContextEntry, ...] = ()) -> None:
        self.entries = {entry.state.key: entry for entry in entries}
        self.writes: list[OperationalContextEntry] = []

    async def current_for_month(
        self, month: date
    ) -> tuple[OperationalContextEntry, ...]:
        return tuple(
            entry for entry in self.entries.values() if entry.state.month == month
        )

    async def current_for_key(
        self, key: str
    ) -> tuple[OperationalContextEntry, ...]:
        entry = self.entries.get(key)
        return (entry,) if entry is not None else ()

    async def save_version(
        self,
        state: OperationalContextState,
        expected: OperationalContextEntry | None,
    ) -> OperationalContextEntry:
        actual = self.entries.get(state.key)
        if actual != expected:
            raise OperationalContextVersionConflict("stale test version")
        entry = OperationalContextEntry(
            page_id=f"version-{len(self.writes) + 1}",
            state=state,
            supersedes=(actual.page_id,) if actual is not None else (),
        )
        self.entries[state.key] = entry
        self.writes.append(entry)
        return entry


def test_new_projection_becomes_presentable_operational_context() -> None:
    result = reconcile_daily_report(report(), [])

    assert len(result.changes) == 1
    change = result.changes[0]
    assert change.kind is OperationalContextChangeKind.CREATED
    assert change.after.kind is OperationalContextKind.PROJECTION_DEVIATION
    assert change.after.band == Decimal("25")
    assert change.after.current_signal == "projection_deviation:25"
    assert change.after.needs_presentation is True
    assert change.after.key.startswith("monitoring.runtime.2026-08.")


def test_acknowledged_projection_escalates_only_when_crossing_the_next_band() -> None:
    first = reconcile_daily_report(report(), []).changes[0].after
    acknowledged = replace(first, acknowledged_signal=first.current_signal)
    next_report = report(as_of=date(2026, 8, 21), groceries_spend="330")

    result = reconcile_daily_report(next_report, [persisted(acknowledged)])
    projection = next(
        change
        for change in result.changes
        if change.after.kind is OperationalContextKind.PROJECTION_DEVIATION
    )

    assert projection.kind is OperationalContextChangeKind.ESCALATED
    assert projection.after.band == Decimal("50")
    assert projection.after.current_signal == "projection_deviation:50"
    assert projection.after.acknowledged_signal == "projection_deviation:25"
    assert projection.after.needs_presentation is True


def test_disappeared_condition_is_resolved_without_erasing_acknowledgement() -> None:
    active = reconcile_daily_report(report(), []).changes[0].after
    active = replace(active, acknowledged_signal=active.current_signal)

    result = reconcile_daily_report(
        report(as_of=date(2026, 8, 21), groceries_spend="100"),
        [persisted(active)],
    )

    change = result.changes[0]
    assert change.kind is OperationalContextChangeKind.RESOLVED
    assert change.after.lifecycle is OperationalContextLifecycle.RESOLVED
    assert change.after.resolved_on == date(2026, 8, 21)
    assert change.after.acknowledged_signal == "projection_deviation:25"
    assert change.after.needs_presentation is False


def test_missing_income_signal_renews_for_each_daily_report() -> None:
    first_result = reconcile_daily_report(report(income=None, groceries_spend="100"), [])
    first = next(
        change.after
        for change in first_result.changes
        if change.after.kind is OperationalContextKind.INCOME_MISSING
    )
    acknowledged = replace(first, acknowledged_signal=first.current_signal)

    second_result = reconcile_daily_report(
        report(as_of=date(2026, 8, 21), income=None, groceries_spend="100"),
        [persisted(acknowledged)],
    )
    second = next(
        change.after
        for change in second_result.changes
        if change.after.kind is OperationalContextKind.INCOME_MISSING
    )

    assert first.current_signal == "income_missing:2026-08-20"
    assert second.current_signal == "income_missing:2026-08-21"
    assert second.occurrence_count == 2
    assert second.needs_presentation is True


def test_actual_overspend_records_proposal_without_claiming_it_was_applied() -> None:
    result = reconcile_daily_report(report(groceries_spend="330"), [])
    overspend = next(
        change.after
        for change in result.changes
        if change.after.kind is OperationalContextKind.ACTUAL_OVERSPEND
    )

    assert overspend.amount == Decimal("20.00")
    assert overspend.proposed_adjustment_amount == Decimal("20.00")
    assert overspend.unresolved_amount == Decimal("0.00")


def test_persistence_service_writes_changes_and_is_idempotent_for_same_report() -> None:
    repository = InMemoryOperationalContextRepository()
    service = OperationalContextService(repository)
    daily_report = report()

    first = asyncio.run(service.reconcile_report(daily_report))
    second = asyncio.run(service.reconcile_report(daily_report))

    assert len(first.written_entries) == 1
    assert second.written_entries == ()
    assert second.reconciliation.changes[0].kind is OperationalContextChangeKind.UNCHANGED
    assert len(repository.writes) == 1


def test_acknowledgement_is_stale_safe_and_versioned() -> None:
    state = reconcile_daily_report(report(), []).changes[0].after
    repository = InMemoryOperationalContextRepository((persisted(state),))
    service = OperationalContextService(repository)

    acknowledged = asyncio.run(
        service.acknowledge_signal(key=state.key, expected_signal=state.current_signal)
    )

    assert acknowledged.state.acknowledged_signal == state.current_signal
    assert acknowledged.supersedes == ("current",)
    with pytest.raises(OperationalContextVersionConflict, match="changed"):
        asyncio.run(
            service.acknowledge_signal(
                key=state.key,
                expected_signal="projection_deviation:50",
            )
        )


def test_confirmed_budget_adjustment_is_persisted_once_and_not_auto_resolved() -> None:
    repository = InMemoryOperationalContextRepository()
    service = OperationalContextService(repository)
    daily_report = report(groceries_spend="330")
    result = BudgetMutationResult(
        status=BudgetMutationStatus.APPLIED,
        operation_id="BADJ-TEST",
        items=(
            AppliedBudgetMutationItem(
                page_id="budget",
                subcategory="Groceries",
                amount_before=Decimal("310"),
                amount_after=Decimal("330"),
                baseline_amount=Decimal("310"),
                already_applied=False,
            ),
        ),
    )

    first = asyncio.run(service.record_budget_adjustment(daily_report, result))
    second = asyncio.run(service.record_budget_adjustment(daily_report, result))
    reconciliation = reconcile_daily_report(daily_report, tuple(repository.entries.values()))

    assert first[0].state.kind is OperationalContextKind.BUDGET_ADJUSTMENT_APPLIED
    assert first[0].state.amount == Decimal("20.00")
    assert first[0].state.current_signal == "budget_adjustment_applied:BADJ-TEST"
    assert second == first
    assert len(repository.writes) == 1
    applied_change = next(
        change
        for change in reconciliation.changes
        if change.after.kind is OperationalContextKind.BUDGET_ADJUSTMENT_APPLIED
    )
    assert applied_change.kind is OperationalContextChangeKind.UNCHANGED
    assert applied_change.after.lifecycle is OperationalContextLifecycle.ACTIVE
