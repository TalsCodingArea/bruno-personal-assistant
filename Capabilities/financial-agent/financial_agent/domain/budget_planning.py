"""Plain contracts for agent-directed monthly budget planning and creation."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from financial_agent.domain.models import Budget, ProgressiveMode
from financial_agent.domain.money import ZERO, money


class BudgetCapBasis(StrEnum):
    """Evidence the agent used when selecting the plan's financial cap."""

    USER_PROVIDED = "user_provided"
    TARGET_MONTH_INCOME = "target_month_income"
    PREVIOUS_MONTH_INCOME = "previous_month_income"
    PREVIOUS_MONTH_BUDGETS = "previous_month_budgets"
    MIXED = "mixed"


class BudgetPlanPurpose(StrEnum):
    """Why one budget allocation exists."""

    REGULAR = "regular"
    FUTURE_EXPENSE = "future_expense"


@dataclass(frozen=True, slots=True)
class BudgetPlanningGuideline:
    """Compact approved Financial Rule relevant to planning judgment."""

    key: str
    statement: str
    kind: str
    scopes: tuple[str, ...]
    page_id: str
    operation_id: str | None
    last_edited_at: datetime | None


@dataclass(frozen=True, slots=True)
class FutureExpenseBudgetNeed:
    """One future expense and its deterministic contribution for the target month."""

    planned_expense_id: str
    name: str
    due_date: date
    remaining_amount: Decimal
    monthly_allocation: Decimal
    status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "remaining_amount", money(self.remaining_amount))
        object.__setattr__(self, "monthly_allocation", money(self.monthly_allocation))


@dataclass(frozen=True, slots=True)
class BudgetPlanningContext:
    """Bounded evidence from which the model may design a monthly budget."""

    target_month: date
    previous_month: date
    existing_target_budgets: tuple[Budget, ...]
    previous_budgets: tuple[Budget, ...]
    target_month_income: Decimal | None
    previous_month_income: Decimal | None
    future_expenses: tuple[FutureExpenseBudgetNeed, ...]
    future_expenses_status: str
    guidelines: tuple[BudgetPlanningGuideline, ...]
    source_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_month", self.target_month.replace(day=1))
        object.__setattr__(self, "previous_month", self.previous_month.replace(day=1))
        if self.target_month_income is not None:
            object.__setattr__(
                self, "target_month_income", money(self.target_month_income)
            )
        if self.previous_month_income is not None:
            object.__setattr__(
                self, "previous_month_income", money(self.previous_month_income)
            )

    @property
    def existing_target_total(self) -> Decimal:
        return money(
            sum((budget.amount for budget in self.existing_target_budgets), ZERO)
        )

    @property
    def previous_budget_total(self) -> Decimal:
        return money(sum((budget.amount for budget in self.previous_budgets), ZERO))

    @property
    def future_expense_allocation(self) -> Decimal:
        return money(
            sum((item.monthly_allocation for item in self.future_expenses), ZERO)
        )


@dataclass(frozen=True, slots=True)
class BudgetPageDraft:
    """One proposed new monthly Budget page."""

    subcategory: str
    amount: Decimal
    progressive: ProgressiveMode
    volatility_percent: Decimal
    purpose: BudgetPlanPurpose
    rationale: str

    def __post_init__(self) -> None:
        subcategory = self.subcategory.strip()
        rationale = self.rationale.strip()
        amount = money(self.amount)
        volatility = money(self.volatility_percent)
        if not subcategory:
            raise ValueError("Budget subcategory cannot be empty")
        if amount <= ZERO:
            raise ValueError("Budget amount must be positive")
        if not ZERO <= volatility <= Decimal("100.00"):
            raise ValueError("Budget volatility_percent must be between 0 and 100")
        if not rationale:
            raise ValueError("Budget rationale cannot be empty")
        if (
            self.purpose is BudgetPlanPurpose.FUTURE_EXPENSE
            and self.progressive is not ProgressiveMode.DISCRETE
        ):
            raise ValueError("Future-expense Budget pages must use Discrete Progressive")
        object.__setattr__(self, "subcategory", subcategory)
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "volatility_percent", volatility)
        object.__setattr__(self, "rationale", rationale)


@dataclass(frozen=True, slots=True)
class BudgetStabilityComparison:
    """How one proposed allocation differs from the previous month."""

    subcategory: str

    previous_amount: Decimal | None
    proposed_amount: Decimal
    delta: Decimal | None

    previous_progressive: ProgressiveMode | None
    proposed_progressive: ProgressiveMode

    previous_volatility_percent: Decimal | None
    proposed_volatility_percent: Decimal

    def __post_init__(self) -> None:
        if self.previous_amount is not None:
            object.__setattr__(
                self,
                "previous_amount",
                money(self.previous_amount),
            )

        object.__setattr__(
            self,
            "proposed_amount",
            money(self.proposed_amount),
        )

        if self.delta is not None:
            object.__setattr__(
                self,
                "delta",
                money(self.delta),
            )

        if self.previous_volatility_percent is not None:
            object.__setattr__(
                self,
                "previous_volatility_percent",
                money(self.previous_volatility_percent),
            )

        object.__setattr__(
            self,
            "proposed_volatility_percent",
            money(self.proposed_volatility_percent),
        )


@dataclass(frozen=True, slots=True)
class MonthlyBudgetPlanDraft:
    """Validated non-persisting plan that may later cross an approval interrupt."""

    operation_id: str
    target_month: date
    source_fingerprint: str
    cap_basis: BudgetCapBasis
    cap_rationale: str
    financial_cap: Decimal
    income_assumption: Decimal | None
    existing_target_total: Decimal
    new_budget_total: Decimal
    total_budget_after: Decimal
    unallocated_cap: Decimal
    variable_reserve_after: Decimal | None
    future_expense_allocation_required: Decimal
    future_expense_allocation_planned: Decimal
    items: tuple[BudgetPageDraft, ...]
    existing_target_budgets: tuple[Budget, ...]
    stability: tuple[BudgetStabilityComparison, ...]
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "financial_cap",
            "income_assumption",
            "existing_target_total",
            "new_budget_total",
            "total_budget_after",
            "unallocated_cap",
            "variable_reserve_after",
            "future_expense_allocation_required",
            "future_expense_allocation_planned",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, money(value))


@dataclass(frozen=True, slots=True)
class CreatedBudgetPage:
    """One confirmed or previously confirmed Budget page creation."""

    page_id: str
    subcategory: str
    amount: Decimal
    already_created: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", money(self.amount))


@dataclass(frozen=True, slots=True)
class BudgetPlanCreationResult:
    """Confirmed result of applying an approved budget plan."""

    operation_id: str
    pages: tuple[CreatedBudgetPage, ...]


class BudgetPlanningError(RuntimeError):
    """Base error for a plan that cannot be safely drafted or created."""


class ExistingTargetBudgetPagesError(ValueError):
    """Requested subcategories already have pages in the target month."""

    def __init__(self, subcategories: tuple[str, ...]) -> None:
        self.subcategories = subcategories
        names = ", ".join(repr(name) for name in subcategories)
        super().__init__(
            f"A target-month Budget page already exists for: {names}"
        )


class BudgetPlanningFreshnessError(BudgetPlanningError):
    """Planning evidence or target Budget pages changed after drafting."""


class BudgetPlanPartialFailure(BudgetPlanningError):
    """Some pages were created before a later Notion operation failed."""
