"""Plain objects for verified, idempotent current-month budget mutations."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from financial_agent.domain.models import ProgressiveMode
from financial_agent.domain.money import money
from financial_agent.domain.monitoring import MonitoringGuideline


class BudgetMutationStatus(StrEnum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    NO_CHANGES = "no_changes"


@dataclass(frozen=True, slots=True)
class BudgetMutationItem:
    """One page-level change and the exact Notion version it expects."""

    page_id: str
    subcategory: str
    month: date
    amount_before: Decimal
    amount_after: Decimal
    progressive: ProgressiveMode | None
    volatility_percent: Decimal | None
    baseline_before: Decimal | None
    last_adjustment_id_before: str | None
    last_adjustment_reason_before: str | None
    last_adjustment_at_before: datetime | None
    last_edited_at_before: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount_before", money(self.amount_before))
        object.__setattr__(self, "amount_after", money(self.amount_after))
        if self.baseline_before is not None:
            object.__setattr__(self, "baseline_before", money(self.baseline_before))
        if self.amount_after < 0:
            raise ValueError("Budget amount_after cannot be negative")


@dataclass(frozen=True, slots=True)
class BudgetMutationProposal:
    """Exact deterministic proposal submitted to preference and freshness gates."""

    operation_id: str
    report_fingerprint: str
    month: date
    as_of: date
    monthly_income: Decimal
    total_budget_before: Decimal
    total_budget_after: Decimal
    reason: str
    items: tuple[BudgetMutationItem, ...]
    guidelines: tuple[MonitoringGuideline, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "monthly_income", money(self.monthly_income))
        object.__setattr__(self, "total_budget_before", money(self.total_budget_before))
        object.__setattr__(self, "total_budget_after", money(self.total_budget_after))


@dataclass(frozen=True, slots=True)
class BudgetPreferenceReview:
    """Veto-only model review of human-authored monitoring guidelines."""

    approved: bool
    summary: str
    blocking_concerns: tuple[str, ...]
    considered_rule_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VerifiedBudgetMutation:
    """Proposal that passed model preference review and two deterministic reads."""

    proposal: BudgetMutationProposal
    preference_review: BudgetPreferenceReview


@dataclass(frozen=True, slots=True)
class AppliedBudgetMutationItem:
    """Confirmed final state for one Notion budget page."""

    page_id: str
    subcategory: str
    amount_before: Decimal
    amount_after: Decimal
    baseline_amount: Decimal
    already_applied: bool


@dataclass(frozen=True, slots=True)
class BudgetMutationResult:
    """Outcome returned after postflight verification or a no-change run."""

    status: BudgetMutationStatus
    operation_id: str | None
    items: tuple[AppliedBudgetMutationItem, ...] = ()


class BudgetMutationError(RuntimeError):
    """Base error for a budget mutation that must not be treated as applied."""


class BudgetMutationFreshnessError(BudgetMutationError):
    """Finance data, policy, or page versions changed during verification."""


class BudgetPreferenceReviewRejected(BudgetMutationError):
    """The preference reviewer found a conflict or omitted relevant rules."""


class BudgetMutationApplyError(BudgetMutationError):
    """Notion rejected a write and every completed update was rolled back."""


class BudgetMutationPartialFailure(BudgetMutationError):
    """A failed multi-page write could not be completely rolled back."""
