"""Persistence and retry tests for the SQLite expense-monitor ledger."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

from financial_agent.domain.expense_monitoring import (
    AlertState,
    ExpenseAlertDraft,
    ExpenseChangedEvent,
    ExpenseEventType,
    ExpenseSeverity,
    MonitoringDecision,
    ProcessedExpenseSnapshot,
)
from financial_agent.integrations.expense_monitor_ledger import SQLiteExpenseMonitorLedger

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
MONTH = date(2026, 8, 1)


def test_sqlite_ledger_persists_decision_snapshot_alert_state_and_outbox(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "expense-monitor.sqlite3"
    first = SQLiteExpenseMonitorLedger(path)
    event = ExpenseChangedEvent("event-1", "page-1", NOW, ExpenseEventType.CREATED)
    decision = MonitoringDecision(
        event_id="event-1",
        source_expense_id="page-1",
        months=(MONTH,),
        scopes=("Takeout",),
        referenced_budget_ids=("budget-1",),
        referenced_rule_keys=("monitoring.projection_bands",),
        calculation_version="expense-impact.v1",
        decision="alert",
        highest_severity=ExpenseSeverity.WARNING,
        summary="Grounded warning.",
        created_at=NOW,
    )
    snapshot = ProcessedExpenseSnapshot(
        page_id="page-1",
        fingerprint="fingerprint",
        final_amount=Decimal("85"),
        category_options=("Food",),
        subcategory_options=("Takeout",),
        occurred_on=date(2026, 8, 20),
        description="Lunch",
        payment_type="Card",
        last_processed_at=NOW,
    )
    alert_state = AlertState(
        month=MONTH,
        scope="Takeout",
        last_severity=ExpenseSeverity.WARNING,
        last_projected_variance=Decimal("75"),
        last_evaluated_at=NOW,
        last_alerted_at=NOW,
    )
    alert = ExpenseAlertDraft(
        event_id="event-1",
        month=MONTH,
        scope="Takeout",
        severity=ExpenseSeverity.WARNING,
        projected_variance=Decimal("75"),
        message="Warning",
        deduplication_reason="First unhealthy observation.",
    )

    committed = asyncio.run(
        first.commit(event, decision, snapshot, (alert_state,), (alert,))
    )
    reopened = SQLiteExpenseMonitorLedger(path)

    assert committed.created is True
    assert asyncio.run(reopened.decision("event-1")) == decision
    assert asyncio.run(reopened.snapshot("page-1")) == snapshot
    assert asyncio.run(reopened.alert_state(MONTH, "Takeout")) == alert_state
    assert len(asyncio.run(reopened.pending_deliveries("event-1"))) == 1

    duplicate = asyncio.run(
        reopened.commit(event, decision, None, (), ())
    )
    assert duplicate.created is False
    assert asyncio.run(reopened.snapshot("page-1")) == snapshot
