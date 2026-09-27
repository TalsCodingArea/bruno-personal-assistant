"""Orchestration tests for the independent event-driven expense graph."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

from financial_agent.domain.expense_monitoring import ExpenseMonitorMode, ExpenseSeverity
from financial_agent.domain.models import Budget, Income, ProgressiveMode, Transaction
from financial_agent.domain.monitoring import MonitoringInputSnapshot, MonitoringPolicy
from financial_agent.graphs.expense_monitor import build_expense_monitor_graph
from financial_agent.integrations.expense_monitor_ledger import InMemoryExpenseMonitorLedger
from financial_agent.integrations.expense_notifications import TraceExpenseAlertNotifier
from financial_agent.services.expense_monitor_workflow import ExpenseMonitorWorkflowService

MONTH = date(2026, 8, 1)
AS_OF = date(2026, 8, 20)
OBSERVED = datetime(2026, 8, 20, 12, tzinfo=UTC)


class ExpenseReader:
    def __init__(self, transaction: Transaction) -> None:
        self.value = transaction
        self.calls: list[str] = []

    async def transaction(self, page_id: str) -> Transaction | None:
        self.calls.append(page_id)
        return self.value if self.value.id == page_id else None


class InputLoader:
    def __init__(self, snapshot: MonitoringInputSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[date] = []

    async def load(self, as_of: date) -> MonitoringInputSnapshot:
        self.calls.append(as_of)
        return self.snapshot


def snapshot(transaction: Transaction) -> MonitoringInputSnapshot:
    income = Decimal("500")
    return MonitoringInputSnapshot(
        month=MONTH,
        as_of=AS_OF,
        transactions=(transaction,),
        incomes=(Income("salary", "Salary", MONTH, income),),
        budgets=(
            Budget(
                "groceries-budget",
                "Groceries",
                MONTH,
                Decimal("100"),
                ProgressiveMode.ACCUMULATED,
                Decimal("0"),
            ),
        ),
        monthly_income=income,
        policy=MonitoringPolicy(protected_subcategories=("Groceries",)),
        policy_sources=(),
    )


def invocation(event_id: str = "event-1") -> dict[str, str]:
    return {
        "event_id": event_id,
        "notion_page_id": "expense-page",
        "observed_at": OBSERVED.isoformat(),
        "event_type": "created",
    }


def test_shadow_graph_commits_grounded_decision_without_delivery_or_budget_write() -> None:
    transaction = Transaction(
        "expense-page",
        "Market",
        AS_OF,
        Decimal("130"),
        category="Food",
        subcategory="Groceries",
    )
    reader = ExpenseReader(transaction)
    inputs = InputLoader(snapshot(transaction))
    ledger = InMemoryExpenseMonitorLedger()
    workflow = ExpenseMonitorWorkflowService(
        reader,
        inputs,
        ledger,
        TraceExpenseAlertNotifier(),
        mode=ExpenseMonitorMode.SHADOW,
    )
    graph = build_expense_monitor_graph(workflow)

    state = asyncio.run(graph.ainvoke(invocation()))

    assert state["duplicate_event"] is False
    assert state["decision"].highest_severity is ExpenseSeverity.CRITICAL
    assert state["decision"].decision == "alert"
    assert "Groceries is at ₪130.00" in state["decision"].summary
    assert "against a ₪100.00 budget" in state["decision"].summary
    assert "1 alert(s)" not in state["decision"].summary
    assert state["commit"].created is True
    assert state["alert_evaluations"][0].alert is not None
    assert state["delivery_receipts"] == ()
    assert ledger.snapshots["expense-page"].final_amount == Decimal("130.00")
    assert reader.calls == ["expense-page"]
    assert inputs.calls == [AS_OF]


def test_same_event_id_is_deduplicated_without_reloading_financial_state() -> None:
    transaction = Transaction(
        "expense-page",
        "Market",
        AS_OF,
        Decimal("130"),
        category="Food",
        subcategory="Groceries",
    )
    reader = ExpenseReader(transaction)
    inputs = InputLoader(snapshot(transaction))
    workflow = ExpenseMonitorWorkflowService(
        reader,
        inputs,
        InMemoryExpenseMonitorLedger(),
        TraceExpenseAlertNotifier(),
    )
    graph = build_expense_monitor_graph(workflow)
    asyncio.run(graph.ainvoke(invocation()))

    second = asyncio.run(graph.ainvoke(invocation()))

    assert second["duplicate_event"] is True
    assert second["decision"].event_id == "event-1"
    assert reader.calls == ["expense-page"]
    assert inputs.calls == [AS_OF]


def test_trace_delivery_uses_transactional_outbox_and_clears_it_after_receipt() -> None:
    transaction = Transaction(
        "expense-page",
        "Market",
        AS_OF,
        Decimal("130"),
        category="Food",
        subcategory="Groceries",
    )
    ledger = InMemoryExpenseMonitorLedger()
    workflow = ExpenseMonitorWorkflowService(
        ExpenseReader(transaction),
        InputLoader(snapshot(transaction)),
        ledger,
        TraceExpenseAlertNotifier(),
        mode=ExpenseMonitorMode.TRACE_DELIVERY,
    )

    state = asyncio.run(build_expense_monitor_graph(workflow).ainvoke(invocation()))

    assert len(state["pending_deliveries"]) == 1
    assert state["delivery_receipts"][0].delivered is True
    assert state["delivery_receipts"][0].channel == "langsmith_trace"
    assert ledger.outbox == {}


def test_deleted_event_uses_prior_snapshot_then_removes_it_from_ledger() -> None:
    transaction = Transaction(
        "expense-page",
        "Market",
        AS_OF,
        Decimal("80"),
        category="Food",
        subcategory="Groceries",
    )
    ledger = InMemoryExpenseMonitorLedger()
    workflow = ExpenseMonitorWorkflowService(
        ExpenseReader(transaction),
        InputLoader(snapshot(transaction)),
        ledger,
        TraceExpenseAlertNotifier(),
    )
    graph = build_expense_monitor_graph(workflow)
    asyncio.run(graph.ainvoke(invocation("created-event")))

    deleted = asyncio.run(
        graph.ainvoke(
            {
                **invocation("deleted-event"),
                "event_type": "deleted",
            }
        )
    )

    assert deleted["diff"].deltas[0].amount == Decimal("-80.00")
    assert "expense-page" not in ledger.snapshots
    assert deleted["decision"].highest_severity is ExpenseSeverity.NONE


def test_invalid_timestamp_fails_before_reading_expense_or_financial_state() -> None:
    transaction = Transaction(
        "expense-page",
        "Market",
        AS_OF,
        Decimal("10"),
    )
    reader = ExpenseReader(transaction)
    inputs = InputLoader(snapshot(transaction))
    graph = build_expense_monitor_graph(
        ExpenseMonitorWorkflowService(
            reader,
            inputs,
            InMemoryExpenseMonitorLedger(),
            TraceExpenseAlertNotifier(),
        )
    )
    invalid = {**invocation(), "observed_at": "2026-08-20T12:00:00"}

    try:
        asyncio.run(graph.ainvoke(invalid))
    except ValueError as exc:
        assert "timezone" in str(exc)
    else:
        raise AssertionError("Expected a timezone-free event to fail")
    assert reader.calls == []
    assert inputs.calls == []
