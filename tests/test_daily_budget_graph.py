"""Focused orchestration tests for the standalone daily LangGraph."""

import asyncio
from datetime import date
from decimal import Decimal

from financial_agent.domain.budget_mutation import (
    AppliedBudgetMutationItem,
    BudgetMutationPartialFailure,
    BudgetMutationResult,
    BudgetMutationStatus,
)
from financial_agent.domain.daily_budget_workflow import DailyBudgetMutationDisposition
from financial_agent.domain.models import Budget, Income, ProgressiveMode, Transaction
from financial_agent.domain.monitoring import (
    DailyBudgetMonitoringReport,
    MonitoringInputSnapshot,
    MonitoringPolicy,
)
from financial_agent.domain.operational_context import (
    OperationalContextEntry,
    OperationalContextPersistenceResult,
    OperationalContextReconciliation,
)
from financial_agent.graphs.daily_budget import build_daily_budget_graph
from financial_agent.services.daily_budget_analysis import analyze_daily_budget_state

AS_OF = date(2026, 8, 20)


def report(
    *, groceries_spend: str = "130", automatic: bool = True
) -> DailyBudgetMonitoringReport:
    transactions = (
        Transaction(
            "expense",
            "Market",
            AS_OF,
            Decimal(groceries_spend),
            category="Food",
            subcategory="Groceries",
        ),
    )
    budgets = (
        Budget(
            "groceries-page",
            "Groceries",
            date(2026, 8, 1),
            Decimal("100"),
            ProgressiveMode.ACCUMULATED,
            Decimal("50"),
        ),
        Budget(
            "entertainment-page",
            "Entertainment",
            date(2026, 8, 1),
            Decimal("400"),
            ProgressiveMode.ACCUMULATED,
            Decimal("50"),
        ),
    )
    income = Decimal("600")
    policy = MonitoringPolicy(automatic_budget_adjustments_enabled=automatic)
    snapshot = MonitoringInputSnapshot(
        month=date(2026, 8, 1),
        as_of=AS_OF,
        transactions=transactions,
        incomes=(Income("salary", "Salary", date(2026, 8, 1), income),),
        budgets=budgets,
        monthly_income=income,
        policy=policy,
        policy_sources=(),
    )
    return DailyBudgetMonitoringReport(
        inputs=snapshot,
        analysis=analyze_daily_budget_state(
            transactions, budgets, AS_OF, income, policy=policy
        ),
    )


class FakeMonitoring:
    def __init__(self, value: DailyBudgetMonitoringReport) -> None:
        self.value = value
        self.calls: list[date] = []

    async def run(self, as_of: date) -> DailyBudgetMonitoringReport:
        self.calls.append(as_of)
        return self.value


class FakeContext:
    def __init__(self) -> None:
        self.reconciled: list[DailyBudgetMonitoringReport] = []
        self.recorded: list[tuple[DailyBudgetMonitoringReport, BudgetMutationResult]] = []

    async def reconcile_report(
        self, value: DailyBudgetMonitoringReport
    ) -> OperationalContextPersistenceResult:
        self.reconciled.append(value)
        reconciliation = OperationalContextReconciliation(
            month=value.analysis.month,
            as_of=value.analysis.as_of,
            changes=(),
        )
        return OperationalContextPersistenceResult(reconciliation, (), ())

    async def record_budget_adjustment(
        self,
        value: DailyBudgetMonitoringReport,
        result: BudgetMutationResult,
    ) -> tuple[OperationalContextEntry, ...]:
        self.recorded.append((value, result))
        return ()


class FakeMutation:
    def __init__(self, value: BudgetMutationResult | Exception) -> None:
        self.value = value
        self.calls: list[DailyBudgetMonitoringReport] = []

    async def apply_report(
        self, candidate: DailyBudgetMonitoringReport
    ) -> BudgetMutationResult:
        self.calls.append(candidate)
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def applied_result() -> BudgetMutationResult:
    return BudgetMutationResult(
        status=BudgetMutationStatus.APPLIED,
        operation_id="BADJ-TEST",
        items=(
            AppliedBudgetMutationItem(
                page_id="groceries-page",
                subcategory="Groceries",
                amount_before=Decimal("100"),
                amount_after=Decimal("130"),
                baseline_amount=Decimal("100"),
                already_applied=False,
            ),
        ),
    )


def test_enabled_rebalance_runs_guarded_mutation_then_records_confirmation() -> None:
    daily_report = report()
    monitoring = FakeMonitoring(daily_report)
    context = FakeContext()
    mutation = FakeMutation(applied_result())
    graph = build_daily_budget_graph(monitoring, context, mutation)

    state = asyncio.run(graph.ainvoke({"as_of": AS_OF.isoformat()}))

    outcome = state["mutation_outcome"]
    assert outcome.disposition is DailyBudgetMutationDisposition.APPLIED
    assert monitoring.calls == [AS_OF]
    assert context.reconciled == [daily_report]
    assert mutation.calls == [daily_report]
    assert context.recorded == [(daily_report, applied_result())]
    assert "applied_context" in state


def test_disabled_rule_persists_observations_but_never_calls_writer() -> None:
    daily_report = report(automatic=False)
    context = FakeContext()
    mutation = FakeMutation(applied_result())
    graph = build_daily_budget_graph(FakeMonitoring(daily_report), context, mutation)

    state = asyncio.run(graph.ainvoke({"as_of": AS_OF.isoformat()}))

    assert state["mutation_outcome"].disposition is DailyBudgetMutationDisposition.DISABLED
    assert context.reconciled == [daily_report]
    assert mutation.calls == []
    assert context.recorded == []


def test_no_change_report_ends_without_entering_mutation_boundary() -> None:
    daily_report = report(groceries_spend="50")
    mutation = FakeMutation(applied_result())
    graph = build_daily_budget_graph(FakeMonitoring(daily_report), FakeContext(), mutation)

    state = asyncio.run(graph.ainvoke({"as_of": AS_OF.isoformat()}))

    assert state["mutation_outcome"].disposition is DailyBudgetMutationDisposition.NOT_NEEDED
    assert mutation.calls == []


def test_partial_failure_is_visible_and_never_claimed_as_applied() -> None:
    daily_report = report()
    context = FakeContext()
    mutation = FakeMutation(BudgetMutationPartialFailure("rollback incomplete"))
    graph = build_daily_budget_graph(FakeMonitoring(daily_report), context, mutation)

    state = asyncio.run(graph.ainvoke({"as_of": AS_OF.isoformat()}))

    outcome = state["mutation_outcome"]
    assert outcome.disposition is DailyBudgetMutationDisposition.PARTIAL_FAILURE
    assert "rollback incomplete" in outcome.reason
    assert context.recorded == []


def test_invalid_studio_input_fails_before_any_financial_read() -> None:
    monitoring = FakeMonitoring(report())
    graph = build_daily_budget_graph(monitoring, FakeContext(), FakeMutation(applied_result()))

    try:
        asyncio.run(graph.ainvoke({"as_of": "20/08/2026"}))
    except ValueError as exc:
        assert "ISO date" in str(exc)
    else:
        raise AssertionError("Expected an invalid date to fail")
    assert monitoring.calls == []
