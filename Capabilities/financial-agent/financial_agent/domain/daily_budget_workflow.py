"""Plain outcomes from one orchestrated daily budget run."""

from dataclasses import dataclass
from enum import StrEnum

from financial_agent.domain.budget_mutation import BudgetMutationResult


class DailyBudgetMutationDisposition(StrEnum):
    """Why the daily workflow did or did not change the Budget database."""

    NOT_NEEDED = "not_needed"
    DISABLED = "disabled"
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    BLOCKED = "blocked"
    FAILED_ROLLED_BACK = "failed_rolled_back"
    PARTIAL_FAILURE = "partial_failure"


@dataclass(frozen=True, slots=True)
class DailyBudgetMutationOutcome:
    """Compact mutation decision suitable for traces and later alert delivery."""

    disposition: DailyBudgetMutationDisposition
    reason: str
    result: BudgetMutationResult | None = None
