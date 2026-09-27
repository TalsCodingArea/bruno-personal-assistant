"""Interfaces through which finance use cases obtain normal domain objects."""

from collections.abc import Mapping
from datetime import date
from typing import Protocol

from financial_agent.domain.budget_mutation import (
    BudgetMutationProposal,
    BudgetMutationResult,
    BudgetPreferenceReview,
    VerifiedBudgetMutation,
)
from financial_agent.domain.budget_planning import (
    BudgetPlanCreationResult,
    CreatedBudgetPage,
    MonthlyBudgetPlanDraft,
)
from financial_agent.domain.expense_monitoring import (
    AlertDeliveryReceipt,
    AlertState,
    ExpenseAlertDraft,
    ExpenseChangedEvent,
    ExpenseMonitorCommit,
    MonitoringDecision,
    PendingAlertDelivery,
    ProcessedExpenseSnapshot,
)
from financial_agent.domain.models import (
    BankMovement,
    Budget,
    ExpenseSettlementTotals,
    Income,
    PlannedExpense,
    Transaction,
)
from financial_agent.domain.monitoring import DailyBudgetMonitoringReport, MonitoringInputSnapshot
from financial_agent.domain.operational_context import (
    OperationalContextEntry,
    OperationalContextPersistenceResult,
    OperationalContextRepair,
    OperationalContextState,
)
from financial_agent.domain.profile import FinancialProfileEntry, FinancialProfileUpdateDraft


class FinanceReader(Protocol):
    async def transactions(
        self, start_date: date, end_date: date, *, limit: int | None = None
    ) -> tuple[Transaction, ...]: ...

    async def incomes(self, start_date: date, end_date: date) -> tuple[Income, ...]: ...

    async def budgets(self, month: date) -> tuple[Budget, ...]: ...

    async def planned_expenses(
        self, start_date: date, end_date: date, *, limit: int | None = None
    ) -> tuple[PlannedExpense, ...]: ...

    async def expense_settlement(self, month: date) -> ExpenseSettlementTotals: ...


class BankMovementReader(Protocol):
    """Bounded read-only access to imported bank movements."""

    async def latest(self, as_of: date) -> BankMovement | None: ...

    async def movements(
        self, start_date: date, end_date: date, *, limit: int = 50
    ) -> tuple[BankMovement, ...]: ...


class ExpensePageReader(Protocol):
    """Resolve exactly one expense page supplied by a trusted trigger."""

    async def transaction(self, page_id: str) -> Transaction | None: ...


class ActiveFinancialRulesReader(Protocol):
    """Read-only view used by context and monitoring input services."""

    async def active_entries(
        self, *, limit: int = 100
    ) -> tuple[FinancialProfileEntry, ...]: ...


class MonitoringInputLoader(Protocol):
    """Read-only input operation required by daily monitoring orchestration."""

    async def load(self, as_of: date) -> MonitoringInputSnapshot: ...


class ExpenseMonitorLedger(Protocol):
    """Durable idempotency, snapshot, decision, alert-state, and outbox store."""

    async def decision(self, event_id: str) -> MonitoringDecision | None: ...

    async def decisions(
        self,
        *,
        event_id: str | None = None,
        page_id: str | None = None,
        month: date | None = None,
        limit: int = 20,
    ) -> tuple[MonitoringDecision, ...]: ...

    async def snapshot(self, page_id: str) -> ProcessedExpenseSnapshot | None: ...

    async def alert_state(self, month: date, scope: str) -> AlertState | None: ...

    async def commit(
        self,
        event: ExpenseChangedEvent,
        decision: MonitoringDecision,
        snapshot: ProcessedExpenseSnapshot | None,
        alert_states: tuple[AlertState, ...],
        alerts: tuple[ExpenseAlertDraft, ...],
    ) -> ExpenseMonitorCommit: ...

    async def pending_deliveries(
        self, event_id: str
    ) -> tuple[PendingAlertDelivery, ...]: ...

    async def record_delivery(self, receipt: AlertDeliveryReceipt) -> None: ...


class ExpenseAlertNotifier(Protocol):
    """External alert boundary; initial application wiring uses a trace-only stub."""

    async def send(self, delivery: PendingAlertDelivery) -> AlertDeliveryReceipt: ...


class DailyBudgetMonitoringRunner(Protocol):
    async def run(self, as_of: date) -> DailyBudgetMonitoringReport: ...


class BudgetMutationRunner(Protocol):
    async def apply_report(
        self, candidate: DailyBudgetMonitoringReport
    ) -> BudgetMutationResult: ...


class OperationalContextManager(Protocol):
    async def reconcile_report(
        self, report: DailyBudgetMonitoringReport
    ) -> OperationalContextPersistenceResult: ...

    async def record_budget_adjustment(
        self,
        report: DailyBudgetMonitoringReport,
        result: BudgetMutationResult,
    ) -> tuple[OperationalContextEntry, ...]: ...


class OperationalContextRepository(Protocol):
    """Versioned persistence required by operational-context reconciliation."""

    async def current_for_month(
        self, month: date
    ) -> tuple[OperationalContextEntry, ...]: ...

    async def current_for_key(
        self, key: str
    ) -> tuple[OperationalContextEntry, ...]: ...

    async def save_version(
        self,
        state: OperationalContextState,
        expected: OperationalContextEntry | None,
    ) -> OperationalContextEntry: ...

    async def repair_duplicate_current_versions(
        self,
    ) -> tuple[OperationalContextRepair, ...]: ...


class BudgetPreferenceReviewer(Protocol):
    """Veto-only review of a deterministic proposal against scoped guidelines."""

    async def review(
        self, proposal: BudgetMutationProposal
    ) -> BudgetPreferenceReview: ...


class BudgetMutationRepository(Protocol):
    """Apply and postflight an already verified mutation."""

    async def apply(self, mutation: VerifiedBudgetMutation) -> BudgetMutationResult: ...


class BudgetPlanCreationRepository(Protocol):
    """Create missing pages from an approved and freshly verified plan."""

    async def create(
        self,
        draft: MonthlyBudgetPlanDraft,
        already_created: tuple[CreatedBudgetPage, ...],
    ) -> BudgetPlanCreationResult: ...


class FinancialProfileRepository(Protocol):
    """Persistence operations needed by the versioned profile service."""

    async def active_entries(
        self, *, key: str | None = None, limit: int = 100
    ) -> tuple[FinancialProfileEntry, ...]: ...

    async def create_active_version(
        self, draft: FinancialProfileUpdateDraft
    ) -> FinancialProfileEntry: ...

    async def mark_superseded(self, page_id: str) -> None: ...


class RecurringTaskManager(Protocol):
    """Persistence boundary for approval-gated recurring assistant tasks."""

    async def create(
        self, *, owner_thread_id: str, prompt: str, cron: str, timezone: str
    ) -> Mapping[str, object]: ...

    async def list_for_owner(
        self, owner_thread_id: str
    ) -> tuple[Mapping[str, object], ...]: ...
