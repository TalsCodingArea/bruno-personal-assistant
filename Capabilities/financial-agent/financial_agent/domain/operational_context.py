"""Plain objects for versioned, short-lived operational finance context."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from financial_agent.domain.money import money

OPERATIONAL_CONTEXT_SCOPE = "Operational Context"
BUDGET_MONITORING_SCOPE = "Budget Monitoring"
OPERATIONAL_CONTEXT_KEY_PREFIX = "monitoring.runtime."


class OperationalContextVersionConflict(RuntimeError):
    """The current Notion version differs from the version being replaced."""


class OperationalContextKind(StrEnum):
    """Signals produced by deterministic daily budget analysis."""

    ACTUAL_OVERSPEND = "actual_overspend"
    BUDGET_ADJUSTMENT_APPLIED = "budget_adjustment_applied"
    BUDGETS_EXCEED_INCOME = "budgets_exceed_income"
    INCOME_MISSING = "income_missing"
    PROJECTION_DEVIATION = "projection_deviation"
    VARIABLE_RESERVE_DEFICIT = "variable_reserve_deficit"


class OperationalContextLifecycle(StrEnum):
    """Whether a previously observed condition still exists."""

    ACTIVE = "active"
    RESOLVED = "resolved"


class OperationalContextChangeKind(StrEnum):
    """How a report changed one stable operational-context identity."""

    CREATED = "created"
    ESCALATED = "escalated"
    REACTIVATED = "reactivated"
    RESOLVED = "resolved"
    UNCHANGED = "unchanged"
    UPDATED = "updated"


@dataclass(frozen=True, slots=True)
class OperationalContextState:
    """Current business state stored as compact JSON in a Notion page."""

    key: str
    name: str
    kind: OperationalContextKind
    month: date
    subcategory: str | None
    lifecycle: OperationalContextLifecycle
    first_observed_on: date
    last_observed_on: date
    resolved_on: date | None
    occurrence_count: int
    current_signal: str
    acknowledged_signal: str | None = None
    amount: Decimal | None = None
    percent: Decimal | None = None
    band: Decimal | None = None
    proposed_adjustment_amount: Decimal | None = None
    unresolved_amount: Decimal | None = None

    def __post_init__(self) -> None:
        if not is_operational_context_key(self.key):
            raise ValueError("Operational context key must use the reserved prefix")
        if self.month.day != 1:
            raise ValueError("Operational context month must be the first day of its month")
        if self.occurrence_count < 1:
            raise ValueError("Operational context occurrence_count must be positive")
        if not self.name.strip() or not self.current_signal.strip():
            raise ValueError("Operational context name and current_signal cannot be empty")
        if self.last_observed_on < self.first_observed_on:
            raise ValueError("last_observed_on cannot precede first_observed_on")
        if self.lifecycle is OperationalContextLifecycle.ACTIVE and self.resolved_on is not None:
            raise ValueError("Active operational context cannot have resolved_on")
        if self.lifecycle is OperationalContextLifecycle.RESOLVED and self.resolved_on is None:
            raise ValueError("Resolved operational context requires resolved_on")
        for field_name in (
            "amount",
            "proposed_adjustment_amount",
            "unresolved_amount",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, money(value))

    @property
    def needs_presentation(self) -> bool:
        """Whether a later conversation should present this exact signal once."""

        return (
            self.lifecycle is OperationalContextLifecycle.ACTIVE
            and self.current_signal != self.acknowledged_signal
        )


@dataclass(frozen=True, slots=True)
class OperationalContextEntry:
    """One persisted current version plus Notion concurrency metadata."""

    page_id: str
    state: OperationalContextState
    supersedes: tuple[str, ...] = ()
    operation_id: str | None = None
    created_at: datetime | None = None
    last_edited_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OperationalContextChange:
    """One deterministic difference between stored state and the latest report."""

    kind: OperationalContextChangeKind
    before: OperationalContextEntry | None
    after: OperationalContextState

    @property
    def requires_write(self) -> bool:
        return self.kind is not OperationalContextChangeKind.UNCHANGED


@dataclass(frozen=True, slots=True)
class OperationalContextReconciliation:
    """Complete deterministic context transition for one report."""

    month: date
    as_of: date
    changes: tuple[OperationalContextChange, ...]

    @property
    def writes(self) -> tuple[OperationalContextChange, ...]:
        return tuple(change for change in self.changes if change.requires_write)

    @property
    def needs_presentation(self) -> tuple[OperationalContextState, ...]:
        return tuple(
            change.after
            for change in self.changes
            if change.after.needs_presentation
        )


@dataclass(frozen=True, slots=True)
class OperationalContextPersistenceResult:
    """Reconciliation plus the current persisted entries after its writes."""

    reconciliation: OperationalContextReconciliation
    current_entries: tuple[OperationalContextEntry, ...]
    written_entries: tuple[OperationalContextEntry, ...]


def is_operational_context_key(key: str) -> bool:
    """Identify the namespace reserved for system-maintained runtime context."""

    return key.strip().casefold().startswith(OPERATIONAL_CONTEXT_KEY_PREFIX)


def is_operational_context_entry(entry: object) -> bool:
    """Identify profile-like entries without importing their concrete type."""

    key = getattr(entry, "key", None)
    return isinstance(key, str) and is_operational_context_key(key)
