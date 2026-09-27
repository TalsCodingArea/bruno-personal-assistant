"""Tests for the read-only daily-monitoring input boundary."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from financial_agent.domain.models import Budget, Income, ProgressiveMode, Transaction
from financial_agent.domain.profile import FinancialProfileEntry, ProfileKind, ProfileStatus
from financial_agent.services.monitoring_inputs import DailyMonitoringInputService
from financial_agent.services.monitoring_policy import (
    MonitoringPolicyConfigurationError,
    compile_monitoring_policy,
)

from tests.fakes import FakeFinanceReader


def rule(
    key: str,
    statement: str,
    *,
    page_id: str | None = None,
    status: ProfileStatus = ProfileStatus.ACTIVE,
) -> FinancialProfileEntry:
    return FinancialProfileEntry(
        page_id=page_id or f"page-{key}",
        name=key,
        key=key,
        kind=ProfileKind.CONSTRAINT,
        scopes=("Budget Monitoring",),
        statement=statement,
        status=status,
        operation_id="CTX-42",
        last_edited_at=datetime(2026, 8, 25, 12, tzinfo=UTC),
    )


class FakeActiveRulesReader:
    def __init__(self, entries: tuple[FinancialProfileEntry, ...] = ()) -> None:
        self.entries = entries
        self.requested_limits: list[int] = []

    async def active_entries(
        self, *, limit: int = 100
    ) -> tuple[FinancialProfileEntry, ...]:
        self.requested_limits.append(limit)
        return self.entries[:limit]


def test_monitoring_policy_uses_defaults_when_controlled_keys_are_absent() -> None:
    compiled = compile_monitoring_policy(
        [rule("spending.groceries_split", "Use 50% of groceries")]
    )

    assert compiled.policy.projection_bands == (
        Decimal("25"),
        Decimal("50"),
        Decimal("75"),
    )
    assert compiled.policy.protected_subcategories == (
        "Rent",
        "Groceries",
        "Electricity",
    )
    assert compiled.policy.preferred_donor_subcategories == ()
    assert compiled.policy.automatic_budget_adjustments_enabled is False
    assert compiled.sources == ()


def test_monitoring_policy_applies_exact_json_rules_with_provenance() -> None:
    compiled = compile_monitoring_policy(
        [
            rule("monitoring.projection_bands", "[30, 55, 80]", page_id="bands"),
            rule(
                "monitoring.protected_subcategories",
                '["Rent", "Groceries", "rent"]',
                page_id="protected",
            ),
            rule(
                "monitoring.preferred_donor_subcategories",
                '["Takeout", "Entertainment"]',
                page_id="donors",
            ),
            rule(
                "monitoring.automatic_budget_adjustments_enabled",
                "true",
                page_id="authorization",
            ),
            rule(
                "monitoring.alert.minimum_material_amount",
                "40",
                page_id="materiality",
            ),
            rule(
                "monitoring.alert.cooldown_hours",
                "12",
                page_id="cooldown",
            ),
            rule(
                "monitoring.reallocation.max_amount",
                "175",
                page_id="reallocation-limit",
            ),
            rule(
                "monitoring.emergency_buffer_amount",
                "500",
                page_id="buffer",
            ),
        ]
    )

    assert compiled.policy.projection_bands == (
        Decimal("30"),
        Decimal("55"),
        Decimal("80"),
    )
    assert compiled.policy.protected_subcategories == ("Rent", "Groceries")
    assert compiled.policy.preferred_donor_subcategories == (
        "Takeout",
        "Entertainment",
    )
    assert compiled.policy.automatic_budget_adjustments_enabled is True
    assert compiled.policy.minimum_material_amount == Decimal("40.00")
    assert compiled.policy.alert_cooldown_hours == 12
    assert compiled.policy.reallocation_max_amount == Decimal("175.00")
    assert compiled.policy.emergency_buffer_amount == Decimal("500.00")
    assert [source.page_id for source in compiled.sources] == [
        "bands",
        "protected",
        "donors",
        "authorization",
        "materiality",
        "cooldown",
        "reallocation-limit",
        "buffer",
    ]
    assert compiled.sources[0].operation_id == "CTX-42"


@pytest.mark.parametrize(
    ("key", "statement"),
    [
        ("monitoring.projection_bands", "25, 50, 75"),
        ("monitoring.projection_bands", '[25, "high"]'),
        ("monitoring.protected_subcategories", '["Rent", 42]'),
        ("monitoring.automatic_budget_adjustments_enabled", '"yes"'),
        ("monitoring.alert.cooldown_hours", "2.5"),
    ],
)
def test_malformed_recognized_policy_rule_fails_closed(key: str, statement: str) -> None:
    with pytest.raises(MonitoringPolicyConfigurationError, match="Financial Rule"):
        compile_monitoring_policy([rule(key, statement)])


def test_duplicate_active_monitoring_rule_fails_closed() -> None:
    entries = [
        rule("monitoring.projection_bands", "[25, 50, 75]", page_id="one"),
        rule("monitoring.projection_bands", "[30, 60, 90]", page_id="two"),
    ]

    with pytest.raises(MonitoringPolicyConfigurationError, match="Multiple Active"):
        compile_monitoring_policy(entries)


def test_input_service_loads_bounded_month_snapshot_and_aggregates_income() -> None:
    finance = FakeFinanceReader(
        transactions=(
            Transaction("past", "Past", date(2026, 8, 5), Decimal("25")),
            Transaction("future", "Future", date(2026, 8, 28), Decimal("100")),
        ),
        incomes=(
            Income("salary", "Salary", date(2026, 8, 1), Decimal("10000")),
            Income("cash", "Cash", date(2026, 8, 10), Decimal("250.50")),
            Income("future", "Future", date(2026, 8, 28), Decimal("500")),
        ),
        budgets=(
            Budget(
                "groceries",
                "Groceries",
                date(2026, 8, 1),
                Decimal("1500"),
                ProgressiveMode.ACCUMULATED,
                Decimal("50"),
            ),
        ),
    )
    rules = FakeActiveRulesReader(
        (rule("monitoring.projection_bands", "[25, 50, 75]"),)
    )

    snapshot = asyncio.run(DailyMonitoringInputService(finance, rules).load(date(2026, 8, 26)))

    assert snapshot.month == date(2026, 8, 1)
    assert snapshot.as_of == date(2026, 8, 26)
    assert [item.id for item in snapshot.transactions] == ["past"]
    assert [item.id for item in snapshot.incomes] == ["salary", "cash"]
    assert snapshot.monthly_income == Decimal("10250.50")
    assert [item.subcategory for item in snapshot.budgets] == ["Groceries"]
    assert snapshot.policy_sources[0].key == "monitoring.projection_bands"
    assert rules.requested_limits == [100]


def test_input_service_preserves_missing_income_as_none() -> None:
    snapshot = asyncio.run(
        DailyMonitoringInputService(FakeFinanceReader(), FakeActiveRulesReader()).load(
            date(2026, 8, 26)
        )
    )

    assert snapshot.incomes == ()
    assert snapshot.monthly_income is None


def test_input_service_keeps_only_noncontrolled_budget_monitoring_guidelines() -> None:
    rules = FakeActiveRulesReader(
        (
            rule("monitoring.automatic_budget_adjustments_enabled", "true"),
            rule("budgeting.keep_entertainment", "Do not reduce Entertainment this month."),
            FinancialProfileEntry(
                page_id="unrelated",
                name="Unrelated",
                key="spending.split",
                kind=ProfileKind.PREFERENCE,
                scopes=("Spending",),
                statement="Use a 50% split.",
                status=ProfileStatus.ACTIVE,
            ),
        )
    )

    snapshot = asyncio.run(
        DailyMonitoringInputService(FakeFinanceReader(), rules).load(date(2026, 8, 26))
    )

    assert snapshot.policy.automatic_budget_adjustments_enabled is True
    assert [item.key for item in snapshot.guidelines] == [
        "budgeting.keep_entertainment"
    ]
