"""Operational expense-monitor ledgers, including the local durable SQLite store."""

import asyncio
import hashlib
import json
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from financial_agent.domain.expense_monitoring import (
    AlertDeliveryReceipt,
    AlertState,
    ExpenseAlertDraft,
    ExpenseChangedEvent,
    ExpenseMonitorCommit,
    ExpenseSeverity,
    MonitoringDecision,
    PendingAlertDelivery,
    ProcessedExpenseSnapshot,
)


class InMemoryExpenseMonitorLedger:
    """Deterministic test/development ledger with the same commit semantics as SQLite."""

    def __init__(self) -> None:
        self._decisions: dict[str, MonitoringDecision] = {}
        self.snapshots: dict[str, ProcessedExpenseSnapshot] = {}
        self.alerts: dict[tuple[date, str], AlertState] = {}
        self.outbox: dict[str, PendingAlertDelivery] = {}

    async def decision(self, event_id: str) -> MonitoringDecision | None:
        return self._decisions.get(event_id)

    async def decisions(
        self,
        *,
        event_id: str | None = None,
        page_id: str | None = None,
        month: date | None = None,
        limit: int = 20,
    ) -> tuple[MonitoringDecision, ...]:
        if limit <= 0:
            return ()
        values = (
            decision
            for decision in self._decisions.values()
            if (event_id is None or decision.event_id == event_id)
            and (page_id is None or decision.source_expense_id == page_id)
            and (month is None or month.replace(day=1) in decision.months)
        )
        return tuple(
            sorted(values, key=lambda item: item.created_at, reverse=True)[:limit]
        )

    async def snapshot(self, page_id: str) -> ProcessedExpenseSnapshot | None:
        return self.snapshots.get(page_id)

    async def alert_state(self, month: date, scope: str) -> AlertState | None:
        return self.alerts.get((month.replace(day=1), scope))

    async def commit(
        self,
        event: ExpenseChangedEvent,
        decision: MonitoringDecision,
        snapshot: ProcessedExpenseSnapshot | None,
        alert_states: tuple[AlertState, ...],
        alerts: tuple[ExpenseAlertDraft, ...],
    ) -> ExpenseMonitorCommit:
        existing = self._decisions.get(event.event_id)
        if existing is not None:
            pending = tuple(
                item.delivery_id
                for item in self.outbox.values()
                if item.event_id == event.event_id
            )
            return ExpenseMonitorCommit(existing, False, pending)
        self._decisions[event.event_id] = decision
        if snapshot is None:
            self.snapshots.pop(event.notion_page_id, None)
        else:
            self.snapshots[event.notion_page_id] = snapshot
        for state in alert_states:
            self.alerts[(state.month, state.scope)] = state
        pending_ids: list[str] = []
        for alert in alerts:
            delivery_id = _delivery_id(alert)
            self.outbox[delivery_id] = PendingAlertDelivery(
                delivery_id=delivery_id,
                event_id=event.event_id,
                alert=alert,
            )
            pending_ids.append(delivery_id)
        return ExpenseMonitorCommit(decision, True, tuple(pending_ids))

    async def pending_deliveries(
        self, event_id: str
    ) -> tuple[PendingAlertDelivery, ...]:
        return tuple(
            item for item in self.outbox.values() if item.event_id == event_id
        )

    async def record_delivery(self, receipt: AlertDeliveryReceipt) -> None:
        if receipt.delivered:
            self.outbox.pop(receipt.delivery_id, None)


class SQLiteExpenseMonitorLedger:
    """Small durable operational store, separate from conversation and Notion rules."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.name:
            raise ValueError("Expense-monitor ledger path must name a SQLite file")

    async def decision(self, event_id: str) -> MonitoringDecision | None:
        return await asyncio.to_thread(self._decision, event_id)

    async def decisions(
        self,
        *,
        event_id: str | None = None,
        page_id: str | None = None,
        month: date | None = None,
        limit: int = 20,
    ) -> tuple[MonitoringDecision, ...]:
        return await asyncio.to_thread(
            self._decisions,
            event_id,
            page_id,
            month,
            limit,
        )

    async def snapshot(self, page_id: str) -> ProcessedExpenseSnapshot | None:
        return await asyncio.to_thread(self._snapshot, page_id)

    async def alert_state(self, month: date, scope: str) -> AlertState | None:
        return await asyncio.to_thread(self._alert_state, month, scope)

    async def commit(
        self,
        event: ExpenseChangedEvent,
        decision: MonitoringDecision,
        snapshot: ProcessedExpenseSnapshot | None,
        alert_states: tuple[AlertState, ...],
        alerts: tuple[ExpenseAlertDraft, ...],
    ) -> ExpenseMonitorCommit:
        return await asyncio.to_thread(
            self._commit,
            event,
            decision,
            snapshot,
            alert_states,
            alerts,
        )

    async def pending_deliveries(
        self, event_id: str
    ) -> tuple[PendingAlertDelivery, ...]:
        return await asyncio.to_thread(self._pending_deliveries, event_id)

    async def record_delivery(self, receipt: AlertDeliveryReceipt) -> None:
        await asyncio.to_thread(self._record_delivery, receipt)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(_SCHEMA)
        return connection

    def _decision(self, event_id: str) -> MonitoringDecision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT decision_json FROM expense_monitor_decisions WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return _decode_decision(row["decision_json"]) if row is not None else None

    def _decisions(
        self,
        event_id: str | None,
        page_id: str | None,
        month: date | None,
        limit: int,
    ) -> tuple[MonitoringDecision, ...]:
        if limit <= 0:
            return ()
        clauses: list[str] = []
        parameters: list[object] = []
        if event_id is not None:
            clauses.append("d.event_id = ?")
            parameters.append(event_id)
        if page_id is not None:
            clauses.append("d.page_id = ?")
            parameters.append(page_id)
        join = ""
        if month is not None:
            join = (
                "JOIN expense_monitor_decision_months m "
                "ON m.event_id = d.event_id"
            )
            clauses.append("m.month = ?")
            parameters.append(month.replace(day=1).isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        query = f"""
            SELECT d.decision_json
            FROM expense_monitor_decisions d
            {join}
            {where}
            ORDER BY d.observed_at DESC, d.event_id DESC
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_decode_decision(row["decision_json"]) for row in rows)

    def _snapshot(self, page_id: str) -> ProcessedExpenseSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM expense_monitor_snapshots WHERE page_id = ?",
                (page_id,),
            ).fetchone()
        return _decode_snapshot(row["snapshot_json"]) if row is not None else None

    def _alert_state(self, month: date, scope: str) -> AlertState | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state_json FROM expense_monitor_alert_states
                WHERE month = ? AND scope = ?
                """,
                (month.replace(day=1).isoformat(), scope),
            ).fetchone()
        return _decode_alert_state(row["state_json"]) if row is not None else None

    def _commit(
        self,
        event: ExpenseChangedEvent,
        decision: MonitoringDecision,
        snapshot: ProcessedExpenseSnapshot | None,
        alert_states: tuple[AlertState, ...],
        alerts: tuple[ExpenseAlertDraft, ...],
    ) -> ExpenseMonitorCommit:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO expense_monitor_decisions
                    (event_id, page_id, observed_at, event_type, decision_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.notion_page_id,
                    event.observed_at.isoformat(),
                    event.event_type.value,
                    _encode_decision(decision),
                ),
            )
            if cursor.rowcount == 0:
                row = connection.execute(
                    "SELECT decision_json FROM expense_monitor_decisions WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()
                assert row is not None
                existing = _decode_decision(row["decision_json"])
                pending = _pending_ids(connection, event.event_id)
                connection.commit()
                return ExpenseMonitorCommit(existing, False, pending)

            connection.executemany(
                """
                INSERT INTO expense_monitor_decision_months (event_id, month)
                VALUES (?, ?)
                """,
                (
                    (event.event_id, month.isoformat())
                    for month in decision.months
                ),
            )

            if snapshot is None:
                connection.execute(
                    "DELETE FROM expense_monitor_snapshots WHERE page_id = ?",
                    (event.notion_page_id,),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO expense_monitor_snapshots (page_id, snapshot_json)
                    VALUES (?, ?)
                    ON CONFLICT(page_id) DO UPDATE SET snapshot_json = excluded.snapshot_json
                    """,
                    (snapshot.page_id, _encode_snapshot(snapshot)),
                )
            for state in alert_states:
                connection.execute(
                    """
                    INSERT INTO expense_monitor_alert_states (month, scope, state_json)
                    VALUES (?, ?, ?)
                    ON CONFLICT(month, scope) DO UPDATE SET state_json = excluded.state_json
                    """,
                    (state.month.isoformat(), state.scope, _encode_alert_state(state)),
                )
            for alert in alerts:
                delivery_id = _delivery_id(alert)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO expense_monitor_outbox
                        (delivery_id, event_id, alert_json, status, attempts, last_error)
                    VALUES (?, ?, ?, 'pending', 0, NULL)
                    """,
                    (delivery_id, event.event_id, _encode_alert(alert)),
                )
            pending = _pending_ids(connection, event.event_id)
            connection.commit()
            return ExpenseMonitorCommit(decision, True, pending)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _pending_deliveries(self, event_id: str) -> tuple[PendingAlertDelivery, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT delivery_id, event_id, alert_json
                FROM expense_monitor_outbox
                WHERE event_id = ? AND status = 'pending'
                ORDER BY delivery_id
                """,
                (event_id,),
            ).fetchall()
        return tuple(
            PendingAlertDelivery(
                delivery_id=row["delivery_id"],
                event_id=row["event_id"],
                alert=_decode_alert(row["alert_json"]),
            )
            for row in rows
        )

    def _record_delivery(self, receipt: AlertDeliveryReceipt) -> None:
        with self._connect() as connection:
            if receipt.delivered:
                connection.execute(
                    """
                    UPDATE expense_monitor_outbox
                    SET status = 'delivered', attempts = attempts + 1,
                        delivered_channel = ?, delivery_detail = ?, last_error = NULL
                    WHERE delivery_id = ?
                    """,
                    (receipt.channel, receipt.detail, receipt.delivery_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE expense_monitor_outbox
                    SET attempts = attempts + 1, last_error = ?
                    WHERE delivery_id = ?
                    """,
                    (receipt.detail, receipt.delivery_id),
                )


def _pending_ids(connection: sqlite3.Connection, event_id: str) -> tuple[str, ...]:
    return tuple(
        row["delivery_id"]
        for row in connection.execute(
            """
            SELECT delivery_id FROM expense_monitor_outbox
            WHERE event_id = ? AND status = 'pending'
            ORDER BY delivery_id
            """,
            (event_id,),
        ).fetchall()
    )


def _delivery_id(alert: ExpenseAlertDraft) -> str:
    identity = f"{alert.event_id}|{alert.month.isoformat()}|{alert.scope}"
    return f"ALERT-{hashlib.sha256(identity.encode()).hexdigest()[:24].upper()}"


def _dumps(items: dict[str, object]) -> str:
    return json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _encode_snapshot(value: ProcessedExpenseSnapshot) -> str:
    return _dumps(
        {
            "page_id": value.page_id,
            "fingerprint": value.fingerprint,
            "final_amount": str(value.final_amount),
            "category_options": value.category_options,
            "subcategory_options": value.subcategory_options,
            "occurred_on": value.occurred_on.isoformat(),
            "description": value.description,
            "payment_type": value.payment_type,
            "last_processed_at": value.last_processed_at.isoformat(),
        }
    )


def _decode_snapshot(value: str) -> ProcessedExpenseSnapshot:
    data = json.loads(value)
    return ProcessedExpenseSnapshot(
        page_id=data["page_id"],
        fingerprint=data["fingerprint"],
        final_amount=Decimal(data["final_amount"]),
        category_options=tuple(data["category_options"]),
        subcategory_options=tuple(data["subcategory_options"]),
        occurred_on=date.fromisoformat(data["occurred_on"]),
        description=data["description"],
        payment_type=data["payment_type"],
        last_processed_at=datetime.fromisoformat(data["last_processed_at"]),
    )


def _encode_alert_state(value: AlertState) -> str:
    return _dumps(
        {
            "month": value.month.isoformat(),
            "scope": value.scope,
            "last_severity": value.last_severity.value,
            "last_projected_variance": str(value.last_projected_variance),
            "last_evaluated_at": value.last_evaluated_at.isoformat(),
            "last_alerted_at": (
                value.last_alerted_at.isoformat()
                if value.last_alerted_at is not None
                else None
            ),
        }
    )


def _decode_alert_state(value: str) -> AlertState:
    data = json.loads(value)
    return AlertState(
        month=date.fromisoformat(data["month"]),
        scope=data["scope"],
        last_severity=ExpenseSeverity(data["last_severity"]),
        last_projected_variance=Decimal(data["last_projected_variance"]),
        last_evaluated_at=datetime.fromisoformat(data["last_evaluated_at"]),
        last_alerted_at=(
            datetime.fromisoformat(data["last_alerted_at"])
            if data["last_alerted_at"] is not None
            else None
        ),
    )


def _encode_alert(value: ExpenseAlertDraft) -> str:
    return _dumps(
        {
            "event_id": value.event_id,
            "month": value.month.isoformat(),
            "scope": value.scope,
            "severity": value.severity.value,
            "projected_variance": str(value.projected_variance),
            "message": value.message,
            "deduplication_reason": value.deduplication_reason,
            "protected_constraint": value.protected_constraint,
        }
    )


def _decode_alert(value: str) -> ExpenseAlertDraft:
    data = json.loads(value)
    return ExpenseAlertDraft(
        event_id=data["event_id"],
        month=date.fromisoformat(data["month"]),
        scope=data["scope"],
        severity=ExpenseSeverity(data["severity"]),
        projected_variance=Decimal(data["projected_variance"]),
        message=data["message"],
        deduplication_reason=data["deduplication_reason"],
        protected_constraint=data["protected_constraint"],
    )


def _encode_decision(value: MonitoringDecision) -> str:
    return _dumps(
        {
            "event_id": value.event_id,
            "source_expense_id": value.source_expense_id,
            "months": tuple(item.isoformat() for item in value.months),
            "scopes": value.scopes,
            "referenced_budget_ids": value.referenced_budget_ids,
            "referenced_rule_keys": value.referenced_rule_keys,
            "calculation_version": value.calculation_version,
            "decision": value.decision,
            "highest_severity": value.highest_severity.value,
            "summary": value.summary,
            "created_at": value.created_at.isoformat(),
        }
    )


def _decode_decision(value: str) -> MonitoringDecision:
    data = json.loads(value)
    return MonitoringDecision(
        event_id=data["event_id"],
        source_expense_id=data["source_expense_id"],
        months=tuple(date.fromisoformat(item) for item in data["months"]),
        scopes=tuple(data["scopes"]),
        referenced_budget_ids=tuple(data["referenced_budget_ids"]),
        referenced_rule_keys=tuple(data["referenced_rule_keys"]),
        calculation_version=data["calculation_version"],
        decision=data["decision"],
        highest_severity=ExpenseSeverity(data["highest_severity"]),
        summary=data["summary"],
        created_at=datetime.fromisoformat(data["created_at"]),
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS expense_monitor_decisions (
    event_id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    decision_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expense_monitor_snapshots (
    page_id TEXT PRIMARY KEY,
    snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expense_monitor_decision_months (
    event_id TEXT NOT NULL,
    month TEXT NOT NULL,
    PRIMARY KEY (event_id, month),
    FOREIGN KEY (event_id) REFERENCES expense_monitor_decisions(event_id)
);
CREATE TABLE IF NOT EXISTS expense_monitor_alert_states (
    month TEXT NOT NULL,
    scope TEXT NOT NULL,
    state_json TEXT NOT NULL,
    PRIMARY KEY (month, scope)
);
CREATE TABLE IF NOT EXISTS expense_monitor_outbox (
    delivery_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    alert_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    last_error TEXT,
    delivered_channel TEXT,
    delivery_detail TEXT,
    FOREIGN KEY (event_id) REFERENCES expense_monitor_decisions(event_id)
);
CREATE INDEX IF NOT EXISTS idx_expense_monitor_outbox_event_status
    ON expense_monitor_outbox (event_id, status);
"""
