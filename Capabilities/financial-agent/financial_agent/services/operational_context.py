"""Application service for versioned operational-context persistence."""

from dataclasses import replace
from hashlib import sha256

from financial_agent.domain.budget_mutation import BudgetMutationResult, BudgetMutationStatus
from financial_agent.domain.monitoring import DailyBudgetMonitoringReport
from financial_agent.domain.operational_context import (
    OPERATIONAL_CONTEXT_KEY_PREFIX,
    OperationalContextEntry,
    OperationalContextKind,
    OperationalContextLifecycle,
    OperationalContextPersistenceResult,
    OperationalContextRepair,
    OperationalContextState,
    OperationalContextVersionConflict,
    is_operational_context_key,
)
from financial_agent.services.operational_reconciliation import reconcile_daily_report
from financial_agent.services.ports import OperationalContextRepository


class OperationalContextService:
    """Reconcile reports and version only changed system-maintained context."""

    def __init__(self, repository: OperationalContextRepository) -> None:
        self.repository = repository

    async def repair_duplicate_current_versions(
        self,
    ) -> tuple[OperationalContextRepair, ...]:
        """Repair every ambiguous Active identity and return confirmed changes."""

        return await self.repository.repair_duplicate_current_versions()

    async def reconcile_report(
        self, report: DailyBudgetMonitoringReport
    ) -> OperationalContextPersistenceResult:
        """Persist deterministic context transitions for one read-only report."""

        existing = await self.repository.current_for_month(report.analysis.month)
        reconciliation = reconcile_daily_report(report, existing)
        current = {entry.state.key: entry for entry in existing}
        written: list[OperationalContextEntry] = []
        for change in reconciliation.writes:
            entry = await self.repository.save_version(change.after, change.before)
            current[entry.state.key] = entry
            written.append(entry)
        return OperationalContextPersistenceResult(
            reconciliation=reconciliation,
            current_entries=tuple(current[key] for key in sorted(current)),
            written_entries=tuple(written),
        )

    async def record_budget_adjustment(
        self,
        report: DailyBudgetMonitoringReport,
        result: BudgetMutationResult,
    ) -> tuple[OperationalContextEntry, ...]:
        """Persist confirmed page-level changes after the guarded writer succeeds."""

        if result.status not in {
            BudgetMutationStatus.APPLIED,
            BudgetMutationStatus.ALREADY_APPLIED,
        }:
            raise ValueError("Only a confirmed budget mutation can be recorded")
        if result.operation_id is None or not result.items:
            raise ValueError("A confirmed mutation requires an operation ID and items")

        written: list[OperationalContextEntry] = []
        for item in result.items:
            normalized = item.subcategory.strip().casefold().encode()
            identity = sha256(normalized).hexdigest()[:16]
            key = (
                f"{OPERATIONAL_CONTEXT_KEY_PREFIX}{report.analysis.month:%Y-%m}."
                f"{OperationalContextKind.BUDGET_ADJUSTMENT_APPLIED.value}.{identity}"
            )
            current = await self.repository.current_for_key(key)
            if len(current) > 1:
                raise OperationalContextVersionConflict(
                    f"Multiple current operational versions found for {key!r}"
                )
            previous = current[0] if current else None
            previous_state = previous.state if previous is not None else None
            signal = f"budget_adjustment_applied:{result.operation_id}"
            occurrence_count = 1
            first_observed_on = report.analysis.as_of
            acknowledged_signal = None
            if previous_state is not None:
                first_observed_on = previous_state.first_observed_on
                acknowledged_signal = previous_state.acknowledged_signal
                occurrence_count = previous_state.occurrence_count
                if previous_state.current_signal != signal:
                    occurrence_count += 1
            state = OperationalContextState(
                key=key,
                name=(
                    f"{report.analysis.month:%Y-%m} · {item.subcategory} · "
                    "Budget Adjustment Applied"
                ),
                kind=OperationalContextKind.BUDGET_ADJUSTMENT_APPLIED,
                month=report.analysis.month,
                subcategory=item.subcategory,
                lifecycle=OperationalContextLifecycle.ACTIVE,
                first_observed_on=first_observed_on,
                last_observed_on=report.analysis.as_of,
                resolved_on=None,
                occurrence_count=occurrence_count,
                current_signal=signal,
                acknowledged_signal=acknowledged_signal,
                amount=item.amount_after - item.amount_before,
            )
            if previous is not None and previous.state == state:
                written.append(previous)
                continue
            written.append(await self.repository.save_version(state, previous))
        return tuple(written)

    async def acknowledge_signal(
        self, *, key: str, expected_signal: str
    ) -> OperationalContextEntry:
        """Record that a later conversation presented one exact current signal."""

        normalized_key = key.strip().casefold()
        signal = expected_signal.strip()
        if not is_operational_context_key(normalized_key) or not signal:
            raise ValueError("A reserved operational key and non-empty signal are required")
        current = await self.repository.current_for_key(normalized_key)
        if len(current) != 1:
            raise OperationalContextVersionConflict(
                f"Expected one current operational version for {normalized_key!r}"
            )
        entry = current[0]
        state = entry.state
        if state.lifecycle is not OperationalContextLifecycle.ACTIVE:
            raise OperationalContextVersionConflict(
                "Cannot acknowledge an operational signal after it resolved"
            )
        if state.current_signal != signal:
            raise OperationalContextVersionConflict(
                "Operational signal changed before acknowledgement"
            )
        if state.acknowledged_signal == signal:
            return entry
        acknowledged = replace(state, acknowledged_signal=signal)
        return await self.repository.save_version(acknowledged, entry)
