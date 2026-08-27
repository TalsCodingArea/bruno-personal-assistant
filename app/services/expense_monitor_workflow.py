"""Application orchestration used by the standalone expense-monitor graph."""

import asyncio
import calendar
from collections.abc import Mapping
from datetime import date, datetime

from app.domain.expense_monitoring import (
    AlertDeliveryReceipt,
    BudgetReallocationDraft,
    ExpenseAlertEvaluation,
    ExpenseChangedEvent,
    ExpenseDiff,
    ExpenseEventType,
    ExpenseImpact,
    ExpenseImpactAssessment,
    ExpenseMonitorCommit,
    ExpenseMonitorMode,
    MonitoringDecision,
    PendingAlertDelivery,
)
from app.domain.monitoring import MonitoringInputSnapshot
from app.services.expense_monitoring import (
    build_monitoring_decision,
    calculate_expense_impact,
    classify_expense_impact,
    draft_budget_reallocation,
    evaluate_expense_alert,
    resolve_expense_diff,
    snapshot_transaction,
)
from app.services.ports import (
    ExpenseAlertNotifier,
    ExpenseMonitorLedger,
    ExpensePageReader,
    MonitoringInputLoader,
)


class ExpenseMonitorWorkflowService:
    """Coordinate reads and persistence while keeping calculations pure."""

    def __init__(
        self,
        expenses: ExpensePageReader,
        inputs: MonitoringInputLoader,
        ledger: ExpenseMonitorLedger,
        notifier: ExpenseAlertNotifier,
        *,
        mode: ExpenseMonitorMode = ExpenseMonitorMode.SHADOW,
    ) -> None:
        self.expenses = expenses
        self.inputs = inputs
        self.ledger = ledger
        self.notifier = notifier
        self.mode = mode

    async def existing_decision(self, event_id: str) -> MonitoringDecision | None:
        return await self.ledger.decision(event_id)

    async def resolve(self, event: ExpenseChangedEvent) -> ExpenseDiff:
        previous = await self.ledger.snapshot(event.notion_page_id)
        transaction = (
            None
            if event.event_type is ExpenseEventType.DELETED
            else await self.expenses.transaction(event.notion_page_id)
        )
        if transaction is None and event.event_type is not ExpenseEventType.DELETED:
            raise ValueError(
                "A created or updated event must resolve to a readable expense page"
            )
        current = (
            snapshot_transaction(transaction, processed_at=event.observed_at)
            if transaction is not None
            else None
        )
        return resolve_expense_diff(event, previous, current)

    async def load_financial_state(
        self, diff: ExpenseDiff
    ) -> dict[date, MonitoringInputSnapshot]:
        months = diff.affected_months
        if not months:
            return {}
        as_of_values = tuple(
            _analysis_date(month, diff.event.observed_at.date()) for month in months
        )
        loaded = await asyncio.gather(
            *(self.inputs.load(as_of) for as_of in as_of_values)
        )
        snapshots = {snapshot.month: snapshot for snapshot in loaded}
        if set(snapshots) != set(months):
            raise ValueError("Expense monitor loaded the wrong monthly financial state")
        return snapshots

    def calculate(
        self,
        diff: ExpenseDiff,
        snapshots: Mapping[date, MonitoringInputSnapshot],
    ) -> ExpenseImpact:
        return calculate_expense_impact(diff, snapshots)

    def classify(
        self,
        impact: ExpenseImpact,
        snapshots: Mapping[date, MonitoringInputSnapshot],
    ) -> tuple[ExpenseImpactAssessment, ...]:
        return classify_expense_impact(impact, snapshots)

    async def evaluate_alerts(
        self,
        event: ExpenseChangedEvent,
        assessments: tuple[ExpenseImpactAssessment, ...],
        snapshots: Mapping[date, MonitoringInputSnapshot],
    ) -> tuple[ExpenseAlertEvaluation, ...]:
        if self.mode is ExpenseMonitorMode.OBSERVE:
            return ()
        previous_states = await asyncio.gather(
            *(
                self.ledger.alert_state(
                    assessment.line.month,
                    assessment.line.scope,
                )
                for assessment in assessments
            )
        )
        return tuple(
            evaluate_expense_alert(
                assessment,
                previous,
                event,
                snapshots[assessment.line.month],
                record_alert=self.mode is ExpenseMonitorMode.TRACE_DELIVERY,
            )
            for assessment, previous in zip(
                assessments,
                previous_states,
                strict=True,
            )
        )

    def draft_reallocations(
        self,
        diff: ExpenseDiff,
        assessments: tuple[ExpenseImpactAssessment, ...],
        snapshots: Mapping[date, MonitoringInputSnapshot],
    ) -> tuple[BudgetReallocationDraft, ...]:
        if self.mode not in {
            ExpenseMonitorMode.DRAFT,
            ExpenseMonitorMode.TRACE_DELIVERY,
        }:
            return ()
        return draft_budget_reallocation(diff, assessments, snapshots)

    async def commit(
        self,
        diff: ExpenseDiff,
        impact: ExpenseImpact,
        assessments: tuple[ExpenseImpactAssessment, ...],
        snapshots: Mapping[date, MonitoringInputSnapshot],
        alert_evaluations: tuple[ExpenseAlertEvaluation, ...],
        reallocations: tuple[BudgetReallocationDraft, ...],
    ) -> ExpenseMonitorCommit:
        proposed_alerts = tuple(
            evaluation.alert
            for evaluation in alert_evaluations
            if evaluation.alert is not None
        )
        decision = build_monitoring_decision(
            diff,
            impact,
            assessments,
            snapshots,
            proposed_alerts,
            reallocations,
        )
        outbox_alerts = (
            proposed_alerts
            if self.mode is ExpenseMonitorMode.TRACE_DELIVERY
            else ()
        )
        return await self.ledger.commit(
            diff.event,
            decision,
            diff.current,
            tuple(evaluation.next_state for evaluation in alert_evaluations),
            outbox_alerts,
        )

    async def pending_deliveries(
        self, event_id: str
    ) -> tuple[PendingAlertDelivery, ...]:
        if self.mode is not ExpenseMonitorMode.TRACE_DELIVERY:
            return ()
        return await self.ledger.pending_deliveries(event_id)

    async def deliver(
        self, deliveries: tuple[PendingAlertDelivery, ...]
    ) -> tuple[AlertDeliveryReceipt, ...]:
        receipts: list[AlertDeliveryReceipt] = []
        for delivery in deliveries:
            receipt = await self.notifier.send(delivery)
            if receipt.delivery_id != delivery.delivery_id:
                raise ValueError("Notifier returned a receipt for the wrong delivery")
            await self.ledger.record_delivery(receipt)
            receipts.append(receipt)
        return tuple(receipts)


def parse_expense_event(
    *,
    event_id: object,
    notion_page_id: object,
    observed_at: object,
    event_type: object,
) -> ExpenseChangedEvent:
    """Validate the JSON-friendly top-level graph input."""

    if not isinstance(event_id, str) or not isinstance(notion_page_id, str):
        raise ValueError("event_id and notion_page_id must be strings")
    if not isinstance(observed_at, str):
        raise ValueError("observed_at must be an ISO timestamp with a timezone offset")
    if not isinstance(event_type, str):
        raise ValueError("event_type must be created, updated, or deleted")
    try:
        timestamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        kind = ExpenseEventType(event_type.strip().casefold())
    except ValueError as exc:
        raise ValueError(
            "Expense event must use an ISO timestamp and a supported event_type"
        ) from exc
    return ExpenseChangedEvent(event_id, notion_page_id, timestamp, kind)


def _analysis_date(month: date, observed_on: date) -> date:
    if month.year == observed_on.year and month.month == observed_on.month:
        return observed_on
    if month < observed_on.replace(day=1):
        return date(
            month.year,
            month.month,
            calendar.monthrange(month.year, month.month)[1],
        )
    return month
