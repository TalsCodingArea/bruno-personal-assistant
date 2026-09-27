"""Plain finance objects shared by integrations, services, and tests."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from financial_agent.domain.money import ZERO, money


class ProgressiveMode(StrEnum):
    """How a regular budget is expected to be consumed during its month."""

    ACCUMULATED = "Accumulated"
    DISCRETE = "Discrete"


@dataclass(frozen=True, slots=True)
class Transaction:
    """One expense using the budget-authoritative Final amount."""

    id: str
    description: str
    occurred_on: date
    final_amount: Decimal
    category: str | None = None
    subcategory: str | None = None
    payment_type: str | None = None
    category_options: tuple[str, ...] = ()
    subcategory_options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "final_amount", money(self.final_amount))
        category, categories = _classification(self.category, self.category_options)
        subcategory, subcategories = _classification(
            self.subcategory, self.subcategory_options
        )
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "subcategory", subcategory)
        object.__setattr__(self, "category_options", categories)
        object.__setattr__(self, "subcategory_options", subcategories)


def _classification(
    canonical: str | None, options: tuple[str, ...]
) -> tuple[str | None, tuple[str, ...]]:
    """Normalize a Notion classification without guessing among multiple values."""

    values = [value.strip() for value in options if value.strip()]
    if canonical is not None and canonical.strip():
        values.insert(0, canonical.strip())
    normalized = tuple(dict.fromkeys(values))
    return (normalized[0] if len(normalized) == 1 else None), normalized


@dataclass(frozen=True, slots=True)
class Income:
    """One received income."""

    id: str
    name: str
    received_on: date
    amount: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", money(self.amount))


@dataclass(frozen=True, slots=True)
class Budget:
    """One monthly sub-category budget."""

    id: str
    subcategory: str
    month: date
    amount: Decimal
    progressive: ProgressiveMode | None = None
    volatility_percent: Decimal | None = None
    baseline_amount: Decimal | None = None
    last_adjustment_id: str | None = None
    last_adjustment_reason: str | None = None
    last_adjustment_at: datetime | None = None
    last_edited_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", money(self.amount))
        object.__setattr__(self, "month", self.month.replace(day=1))
        if self.baseline_amount is not None:
            object.__setattr__(self, "baseline_amount", money(self.baseline_amount))
        if self.volatility_percent is not None:
            volatility = money(self.volatility_percent)
            if not ZERO <= volatility <= Decimal("100.00"):
                raise ValueError("volatility_percent must be between 0 and 100")
            object.__setattr__(self, "volatility_percent", volatility)


@dataclass(frozen=True, slots=True)
class PlannedExpense:
    """A future cost the user is preparing for."""

    id: str
    name: str
    target_amount: Decimal
    due_date: date
    saved_amount: Decimal = ZERO

    def __post_init__(self) -> None:
        target = money(self.target_amount)
        saved = money(self.saved_amount)
        if target <= ZERO:
            raise ValueError("target_amount must be positive")
        if saved < ZERO:
            raise ValueError("saved_amount cannot be negative")
        object.__setattr__(self, "target_amount", target)
        object.__setattr__(self, "saved_amount", saved)


@dataclass(frozen=True, slots=True)
class CategorySuggestion:
    """A deterministic category suggestion backed by matching history."""

    transaction_id: str
    category: str
    subcategory: str
    confidence: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class CategoryUpdateDraft:
    """A proposed category change with no side effect."""

    transaction_id: str
    category: str
    subcategory: str
    reason: str


@dataclass(frozen=True, slots=True)
class PlannedExpenseDraft:
    """A proposed future expense with no side effect."""

    name: str
    target_amount: Decimal
    due_date: date
    saved_amount: Decimal
    monthly_allocation: Decimal
    saving_starts_on: date


@dataclass(frozen=True, slots=True)
class BankMovement:
    """One read-only movement from the Bank Movement data source."""

    id: str
    title: str
    occurred_on: date
    direction: str
    amount: Decimal
    balance_after: Decimal
    description: str = ""
    action: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", money(self.amount))
        object.__setattr__(self, "balance_after", money(self.balance_after))


@dataclass(frozen=True, slots=True)
class ExpenseSettlementTotals:
    """Raw card charge and shared-expense reimbursement for one expense month."""

    month: date
    credit_charges: Decimal
    mutual_formula_total: Decimal
    expected_reimbursement: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "month", self.month.replace(day=1))
        for name in (
            "credit_charges",
            "mutual_formula_total",
            "expected_reimbursement",
        ):
            object.__setattr__(self, name, money(getattr(self, name)))
