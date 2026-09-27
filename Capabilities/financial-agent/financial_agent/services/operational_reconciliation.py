"""Pure reconciliation of daily reports into versioned operational context."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from hashlib import sha256

from financial_agent.domain.monitoring import BudgetObservation, DailyBudgetMonitoringReport
from financial_agent.domain.operational_context import (
    OPERATIONAL_CONTEXT_KEY_PREFIX,
    OperationalContextChange,
    OperationalContextChangeKind,
    OperationalContextEntry,
    OperationalContextKind,
    OperationalContextLifecycle,
    OperationalContextReconciliation,
    OperationalContextState,
)


class OperationalContextConflict(RuntimeError):
    """Stored operational context is ambiguous or incompatible with the report."""


def reconcile_daily_report(
    report: DailyBudgetMonitoringReport,
    existing_entries: tuple[OperationalContextEntry, ...] | list[OperationalContextEntry],
) -> OperationalContextReconciliation:
    """Produce stable create/update/resolve transitions without performing I/O."""

    as_of = report.analysis.as_of
    month = report.analysis.month
    if report.inputs.as_of != as_of or report.inputs.month != month:
        raise OperationalContextConflict("Report inputs and analysis dates do not match")
    existing = _index_existing(existing_entries, month)
    desired = _desired_states(report)
    changes: list[OperationalContextChange] = []

    for key, candidate in desired.items():
        previous = existing.get(key)
        after = _merge_observed(candidate, previous, as_of)
        changes.append(
            OperationalContextChange(
                kind=_observed_change_kind(previous, after),
                before=previous,
                after=after,
            )
        )

    for key, previous in existing.items():
        if key in desired:
            continue
        # Applied adjustments are point-in-time events, not conditions inferred from
        # today's report. Keep them until the later retention policy archives the month.
        if previous.state.kind is OperationalContextKind.BUDGET_ADJUSTMENT_APPLIED:
            changes.append(
                OperationalContextChange(
                    kind=OperationalContextChangeKind.UNCHANGED,
                    before=previous,
                    after=previous.state,
                )
            )
            continue
        if previous.state.lifecycle is OperationalContextLifecycle.RESOLVED:
            changes.append(
                OperationalContextChange(
                    kind=OperationalContextChangeKind.UNCHANGED,
                    before=previous,
                    after=previous.state,
                )
            )
            continue
        resolved = replace(
            previous.state,
            lifecycle=OperationalContextLifecycle.RESOLVED,
            resolved_on=as_of,
        )
        changes.append(
            OperationalContextChange(
                kind=OperationalContextChangeKind.RESOLVED,
                before=previous,
                after=resolved,
            )
        )

    return OperationalContextReconciliation(
        month=month,
        as_of=as_of,
        changes=tuple(sorted(changes, key=lambda change: change.after.key)),
    )


def _index_existing(
    entries: tuple[OperationalContextEntry, ...] | list[OperationalContextEntry],
    month: date,
) -> dict[str, OperationalContextEntry]:
    result: dict[str, OperationalContextEntry] = {}
    for entry in entries:
        if entry.state.month != month:
            raise OperationalContextConflict(
                f"Operational context {entry.state.key!r} belongs to another month"
            )
        if entry.state.key in result:
            raise OperationalContextConflict(
                f"Multiple current operational versions found for {entry.state.key!r}"
            )
        result[entry.state.key] = entry
    return result


def _desired_states(
    report: DailyBudgetMonitoringReport,
) -> dict[str, OperationalContextState]:
    result: dict[str, OperationalContextState] = {}
    for observation in report.analysis.observations:
        kind = OperationalContextKind(observation.kind.value)
        state = _state_from_observation(observation, kind)
        _insert_unique(result, state)
    for alert in report.analysis.alerts:
        kind = OperationalContextKind.ACTUAL_OVERSPEND
        key = _context_key(report.analysis.month, kind, alert.subcategory)
        state = OperationalContextState(
            key=key,
            name=_context_name(kind, report.analysis.month, alert.subcategory),
            kind=kind,
            month=report.analysis.month,
            subcategory=alert.subcategory,
            lifecycle=OperationalContextLifecycle.ACTIVE,
            first_observed_on=report.analysis.as_of,
            last_observed_on=report.analysis.as_of,
            resolved_on=None,
            occurrence_count=1,
            current_signal=f"{kind.value}:{report.analysis.as_of.isoformat()}",
            amount=alert.actual_overspend,
            proposed_adjustment_amount=alert.adjustment_amount,
            unresolved_amount=alert.unresolved_amount,
        )
        _insert_unique(result, state)
    return result


def _state_from_observation(
    observation: BudgetObservation,
    kind: OperationalContextKind,
) -> OperationalContextState:
    signal = (
        f"{kind.value}:{_decimal_text(observation.band)}"
        if kind is OperationalContextKind.PROJECTION_DEVIATION
        else f"{kind.value}:{observation.as_of.isoformat()}"
    )
    return OperationalContextState(
        key=_context_key(observation.month, kind, observation.subcategory),
        name=_context_name(kind, observation.month, observation.subcategory),
        kind=kind,
        month=observation.month,
        subcategory=observation.subcategory,
        lifecycle=OperationalContextLifecycle.ACTIVE,
        first_observed_on=observation.as_of,
        last_observed_on=observation.as_of,
        resolved_on=None,
        occurrence_count=1,
        current_signal=signal,
        amount=observation.amount,
        percent=observation.percent,
        band=observation.band,
    )


def _insert_unique(
    result: dict[str, OperationalContextState], state: OperationalContextState
) -> None:
    if state.key in result:
        raise OperationalContextConflict(
            f"Report produced duplicate operational context key {state.key!r}"
        )
    result[state.key] = state


def _merge_observed(
    candidate: OperationalContextState,
    previous: OperationalContextEntry | None,
    as_of: date,
) -> OperationalContextState:
    if previous is None:
        return candidate
    prior = previous.state
    occurrence_count = prior.occurrence_count
    if prior.last_observed_on != as_of:
        occurrence_count += 1
    return replace(
        candidate,
        first_observed_on=prior.first_observed_on,
        occurrence_count=occurrence_count,
        acknowledged_signal=prior.acknowledged_signal,
    )


def _observed_change_kind(
    previous: OperationalContextEntry | None,
    after: OperationalContextState,
) -> OperationalContextChangeKind:
    if previous is None:
        return OperationalContextChangeKind.CREATED
    before = previous.state
    if before == after:
        return OperationalContextChangeKind.UNCHANGED
    if before.lifecycle is OperationalContextLifecycle.RESOLVED:
        return OperationalContextChangeKind.REACTIVATED
    if (
        before.kind is OperationalContextKind.PROJECTION_DEVIATION
        and before.band is not None
        and after.band is not None
        and after.band > before.band
    ):
        return OperationalContextChangeKind.ESCALATED
    return OperationalContextChangeKind.UPDATED


def _context_key(
    month: date,
    kind: OperationalContextKind,
    subcategory: str | None,
) -> str:
    identity = "global"
    if subcategory is not None:
        normalized = subcategory.strip().casefold().encode()
        identity = sha256(normalized).hexdigest()[:16]
    return f"{OPERATIONAL_CONTEXT_KEY_PREFIX}{month:%Y-%m}.{kind.value}.{identity}"


def _context_name(
    kind: OperationalContextKind,
    month: date,
    subcategory: str | None,
) -> str:
    subject = subcategory or "Overall budget"
    label = kind.value.replace("_", " ").title()
    return f"{month:%Y-%m} · {subject} · {label}"


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        raise OperationalContextConflict("Projection observation requires a band")
    return format(value, "f")
