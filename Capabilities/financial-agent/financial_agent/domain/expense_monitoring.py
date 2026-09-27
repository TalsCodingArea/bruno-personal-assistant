"""Domain vocabulary for event-driven expense monitoring."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from financial_agent.domain.models import ProgressiveMode
from financial_agent.domain.money import ZERO, money


class ExpenseEventType(StrEnum):
    """Changes a future expense trigger may report."""

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


class ExpenseMonitorMode(StrEnum):
    """Progressive rollout levels for the independent monitoring graph."""

    OBSERVE = "observe"
    SHADOW = "shadow"
    DRAFT = "draft"
    TRACE_DELIVERY = "trace_delivery"


class ExpenseSeverity(StrEnum):
    """Policy result for one affected budget or variable-spending pool."""

    NONE = "none"
    INFORMATIONAL = "informational"
    WATCH = "watch"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class ExpenseChangedEvent:
    """Stable contract accepted from a future Notion expense trigger."""

    event_id: str
    notion_page_id: str
    observed_at: datetime
    event_type: ExpenseEventType

    def __post_init__(self) -> None:
        event_id = self.event_id.strip()
        page_id = self.notion_page_id.strip()
        if not event_id:
            raise ValueError("event_id cannot be empty")
        if not page_id:
            raise ValueError("notion_page_id cannot be empty")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must include a timezone offset")
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "notion_page_id", page_id)


@dataclass(frozen=True, slots=True)
class ProcessedExpenseSnapshot:
    """Last committed interpretation of one Notion expense page."""

    page_id: str
    fingerprint: str
    final_amount: Decimal
    category_options: tuple[str, ...]
    subcategory_options: tuple[str, ...]
    occurred_on: date
    description: str
    payment_type: str | None
    last_processed_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "final_amount", money(self.final_amount))

    @property
    def category(self) -> str | None:
        return self.category_options[0] if len(self.category_options) == 1 else None

    @property
    def subcategory(self) -> str | None:
        return (
            self.subcategory_options[0]
            if len(self.subcategory_options) == 1
            else None
        )


@dataclass(frozen=True, slots=True)
class ExpenseDelta:
    """One signed contribution removed from or added to a monthly classification."""

    month: date
    occurred_on: date
    subcategory: str | None
    subcategory_options: tuple[str, ...]
    amount: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "month", self.month.replace(day=1))
        object.__setattr__(self, "amount", money(self.amount))

    @property
    def scope(self) -> str:
        if self.subcategory is not None:
            return self.subcategory
        if len(self.subcategory_options) > 1:
            return "Ambiguous"
        return "Variable"


@dataclass(frozen=True, slots=True)
class ExpenseDiff:
    """Resolved page change relative to the last committed snapshot."""

    event: ExpenseChangedEvent
    previous: ProcessedExpenseSnapshot | None
    current: ProcessedExpenseSnapshot | None
    deltas: tuple[ExpenseDelta, ...]
    changed: bool
    reason: str

    @property
    def affected_months(self) -> tuple[date, ...]:
        return tuple(sorted({delta.month for delta in self.deltas}))


@dataclass(frozen=True, slots=True)
class ExpenseImpactLine:
    """Deterministic effect on one budget or variable-pool scope."""

    month: date
    as_of: date
    subcategory: str | None
    scope: str
    expense_class: str
    progressive: ProgressiveMode | None
    budget: Decimal | None
    spent_before: Decimal
    expense_delta: Decimal
    spent_after: Decimal
    expected_spend_by_today: Decimal | None
    projected_month_end: Decimal | None
    budget_variance_projection: Decimal | None
    volatility_percent: Decimal | None
    variable_pool_before: Decimal | None = None
    variable_pool_after: Decimal | None = None
    protected: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "budget",
            "spent_before",
            "expense_delta",
            "spent_after",
            "expected_spend_by_today",
            "projected_month_end",
            "budget_variance_projection",
            "volatility_percent",
            "variable_pool_before",
            "variable_pool_after",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, money(value))


@dataclass(frozen=True, slots=True)
class ExpenseImpact:
    """Complete deterministic impact of one expense-page change."""

    expense_id: str
    event_id: str
    lines: tuple[ExpenseImpactLine, ...]
    net_monthly_spending_change: Decimal
    calculation_version: str = "expense-impact.v1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "net_monthly_spending_change",
            money(self.net_monthly_spending_change),
        )


@dataclass(frozen=True, slots=True)
class ExpenseImpactAssessment:
    """Policy classification for one deterministic impact line."""

    line: ExpenseImpactLine
    severity: ExpenseSeverity
    reason: str


@dataclass(frozen=True, slots=True)
class AlertState:
    """Last known health and alert point for one monthly scope."""

    month: date
    scope: str
    last_severity: ExpenseSeverity
    last_projected_variance: Decimal
    last_evaluated_at: datetime
    last_alerted_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "last_projected_variance", money(self.last_projected_variance)
        )


@dataclass(frozen=True, slots=True)
class ExpenseAlertDraft:
    """Grounded alert text and the policy reason it was produced."""

    event_id: str
    month: date
    scope: str
    severity: ExpenseSeverity
    projected_variance: Decimal
    message: str
    deduplication_reason: str
    protected_constraint: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "projected_variance", money(self.projected_variance))


@dataclass(frozen=True, slots=True)
class ExpenseAlertEvaluation:
    """Alert output plus the state that must be committed atomically."""

    assessment: ExpenseImpactAssessment
    alert: ExpenseAlertDraft | None
    next_state: AlertState


@dataclass(frozen=True, slots=True)
class BudgetReallocationChange:
    """One side of a total-preserving budget reallocation draft."""

    subcategory: str
    current: Decimal
    proposed: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "current", money(self.current))
        object.__setattr__(self, "proposed", money(self.proposed))


@dataclass(frozen=True, slots=True)
class BudgetReallocationDraft:
    """Non-persisting response to a material event impact."""

    source_expense_id: str
    month: date
    reason: str
    changes: tuple[BudgetReallocationChange, ...]
    total_before: Decimal
    total_after: Decimal
    protected_priorities_affected: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "total_before", money(self.total_before))
        object.__setattr__(self, "total_after", money(self.total_after))
        if self.total_before != self.total_after:
            raise ValueError("A reallocation draft must preserve its total allocation")
        if self.protected_priorities_affected:
            raise ValueError("A reallocation draft cannot affect protected priorities")


@dataclass(frozen=True, slots=True)
class MonitoringDecision:
    """Compact grounded record connecting later corrections to this run."""

    event_id: str
    source_expense_id: str
    months: tuple[date, ...]
    scopes: tuple[str, ...]
    referenced_budget_ids: tuple[str, ...]
    referenced_rule_keys: tuple[str, ...]
    calculation_version: str
    decision: str
    highest_severity: ExpenseSeverity
    summary: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExpenseMonitorCommit:
    """Result of atomically committing a monitoring decision and outbox rows."""

    decision: MonitoringDecision
    created: bool
    pending_delivery_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PendingAlertDelivery:
    """One transactional-outbox item awaiting notification delivery."""

    delivery_id: str
    event_id: str
    alert: ExpenseAlertDraft


@dataclass(frozen=True, slots=True)
class AlertDeliveryReceipt:
    """Result from a notification boundary, including trace-only stubs."""

    delivery_id: str
    delivered: bool
    channel: str
    detail: str


NO_VARIANCE = ZERO
