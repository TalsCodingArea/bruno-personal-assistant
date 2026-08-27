"""Tests for the read-to-calculation daily monitoring bridge."""

import asyncio
from datetime import date
from decimal import Decimal

import pytest

from app.domain.models import Budget, Income, ProgressiveMode, Transaction
from app.domain.monitoring import (
    AnalysisStatus,
    MonitoringInputSnapshot,
    MonitoringPolicy,
)
from app.services.daily_budget_monitoring import DailyBudgetMonitoringService
from app.tools.serialization import jsonable

AS_OF = date(2026, 8, 20)


class FakeMonitoringInputLoader:
    def __init__(self, snapshot: MonitoringInputSnapshot) -> None:
        self.snapshot = snapshot
        self.requested_dates: list[date] = []

    async def load(self, as_of: date) -> MonitoringInputSnapshot:
        self.requested_dates.append(as_of)
        return self.snapshot


def snapshot(*, income: Decimal | None = Decimal("600")) -> MonitoringInputSnapshot:
    incomes = (
        (Income("salary", "Salary", date(2026, 8, 1), income),)
        if income is not None
        else ()
    )
    return MonitoringInputSnapshot(
        month=date(2026, 8, 1),
        as_of=AS_OF,
        transactions=(
            Transaction(
                "groceries-expense",
                "Market",
                date(2026, 8, 15),
                Decimal("130"),
                category="Food",
                subcategory="Groceries",
            ),
            Transaction(
                "uncategorized-expense",
                "Unknown",
                date(2026, 8, 18),
                Decimal("50"),
            ),
        ),
        incomes=incomes,
        budgets=(
            Budget(
                "groceries-budget",
                "Groceries",
                date(2026, 8, 1),
                Decimal("100"),
                ProgressiveMode.ACCUMULATED,
                Decimal("50"),
            ),
            Budget(
                "entertainment-budget",
                "Entertainment",
                date(2026, 8, 1),
                Decimal("400"),
                ProgressiveMode.ACCUMULATED,
                Decimal("50"),
            ),
        ),
        monthly_income=income,
        policy=MonitoringPolicy(),
        policy_sources=(),
    )


def test_service_returns_snapshot_and_analysis_as_one_report() -> None:
    loader = FakeMonitoringInputLoader(snapshot())

    report = asyncio.run(DailyBudgetMonitoringService(loader).run(AS_OF))

    assert report.inputs is loader.snapshot
    assert loader.requested_dates == [AS_OF]
    assert report.analysis.status is AnalysisStatus.READY
    assert report.analysis.actual_variable_spend == Decimal("50.00")
    assert report.analysis.adjustment_plan is not None
    assert report.analysis.adjustment_plan.budget_changes[0].subcategory == "Groceries"
    assert report.analysis.adjustment_plan.budget_changes[0].budget_after == Decimal(
        "130.00"
    )


def test_report_is_json_safe_for_future_persistence_without_storing_it() -> None:
    report = asyncio.run(
        DailyBudgetMonitoringService(FakeMonitoringInputLoader(snapshot())).run(AS_OF)
    )

    serialized = jsonable(report)

    assert isinstance(serialized, dict)
    assert serialized["inputs"]["as_of"] == "2026-08-20"
    assert serialized["analysis"]["monthly_income"] == "600.00"
    assert serialized["analysis"]["adjustment_plan"]["budget_changes"][0] == {
        "subcategory": "Groceries",
        "budget_before": "100.00",
        "budget_after": "130.00",
    }


def test_missing_income_flows_to_blocked_analysis_without_special_case_in_service() -> None:
    report = asyncio.run(
        DailyBudgetMonitoringService(
            FakeMonitoringInputLoader(snapshot(income=None))
        ).run(AS_OF)
    )

    assert report.analysis.status is AnalysisStatus.INCOME_MISSING
    assert report.analysis.adjustment_plan is None


def test_service_rejects_snapshot_for_a_different_run_date() -> None:
    mismatched = snapshot()
    loader = FakeMonitoringInputLoader(
        MonitoringInputSnapshot(
            month=mismatched.month,
            as_of=date(2026, 8, 19),
            transactions=mismatched.transactions,
            incomes=mismatched.incomes,
            budgets=mismatched.budgets,
            monthly_income=mismatched.monthly_income,
            policy=mismatched.policy,
            policy_sources=mismatched.policy_sources,
        )
    )

    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(DailyBudgetMonitoringService(loader).run(AS_OF))
