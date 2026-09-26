"""Domain vocabulary for deterministic daily budget monitoring."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from financial_agent.domain.models import Budget, Income, ProgressiveMode, Transaction
from financial_agent.domain.money import ZERO, money


class AnalysisStatus(StrEnum):
    """Whether the analysis has enough authoritative data to plan changes."""

    READY = "ready"
    INCOME_MISSING = "income_missing"


class ObservationKind(StrEnum):
    """Structured facts that later layers may place in conversation context."""

    BUDGETS_EXCEED_INCOME = "budgets_exceed_income"
    INCOME_MISSING = "income_missing"
    PROJECTION_DEVIATION = "projection_deviation"
    VARIABLE_RESERVE_DEFICIT = "variable_reserve_deficit"


class FundingSourceKind(StrEnum):
    """The source used to fund a proposed budget movement."""

    BUDGET = "budget"
    VARIABLE_RESERVE = "variable_reserve"


@dataclass(frozen=True, slots=True)
class MonitoringPolicy:
    """Adjustable monitoring rules, eventually loaded from Financial Rules."""

    projection_bands: tuple[Decimal, ...] = (
        Decimal("25"),
        Decimal("50"),
        Decimal("75"),
    )
    protected_subcategories: tuple[str, ...] = (
        "Rent",
        "Groceries",
        "Electricity",
    )
    preferred_donor_subcategories: tuple[str, ...] = ()
    automatic_budget_adjustments_enabled: bool = False
    minimum_material_amount: Decimal = Decimal("25.00")
    alert_cooldown_hours: int = 24
    material_projection_increase: Decimal = Decimal("50.00")
    reallocation_enabled: bool = True
    reallocation_max_amount: Decimal = Decimal("150.00")
    emergency_buffer_amount: Decimal = ZERO

    def __post_init__(self) -> None:
        if not isinstance(self.automatic_budget_adjustments_enabled, bool):
            raise TypeError("automatic_budget_adjustments_enabled must be boolean")
        if not isinstance(self.reallocation_enabled, bool):
            raise TypeError("reallocation_enabled must be boolean")
        if not self.projection_bands:
            raise ValueError("projection_bands cannot be empty")
        if any(band <= 0 for band in self.projection_bands):
            raise ValueError("projection_bands must be positive")
        if tuple(sorted(set(self.projection_bands))) != self.projection_bands:
            raise ValueError("projection_bands must be unique and ascending")
        if self.alert_cooldown_hours <= 0:
            raise ValueError("alert_cooldown_hours must be positive")
        for name in (
            "minimum_material_amount",
            "material_projection_increase",
            "reallocation_max_amount",
            "emergency_buffer_amount",
        ):
            value = money(getattr(self, name))
            if value < ZERO:
                raise ValueError(f"{name} cannot be negative")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class MonitoringPolicySource:
    """Compact trace of the Active Financial Rules page behind one override."""

    key: str
    page_id: str
    operation_id: str | None
    last_edited_at: datetime | None


@dataclass(frozen=True, slots=True)
class MonitoringGuideline:
    """Compact human-authored rule eligible for preference review."""

    key: str
    statement: str
    kind: str
    scopes: tuple[str, ...]
    page_id: str
    operation_id: str | None
    last_edited_at: datetime | None


@dataclass(frozen=True, slots=True)
class MonitoringInputSnapshot:
    """Normal read-only inputs for one deterministic daily analysis."""

    month: date
    as_of: date
    transactions: tuple[Transaction, ...]
    incomes: tuple[Income, ...]
    budgets: tuple[Budget, ...]
    monthly_income: Decimal | None
    policy: MonitoringPolicy
    policy_sources: tuple[MonitoringPolicySource, ...]
    guidelines: tuple[MonitoringGuideline, ...] = ()


@dataclass(frozen=True, slots=True)
class CategoryBudgetAnalysis:
    """The current and projected state of one budgeted subcategory."""

    subcategory: str
    budget: Decimal
    actual_spend: Decimal
    projected_spend: Decimal
    actual_overspend: Decimal
    projection_deviation_percent: Decimal | None
    projection_band: Decimal | None
    progressive: ProgressiveMode | None
    volatility_percent: Decimal | None
    protected: bool


@dataclass(frozen=True, slots=True)
class BudgetObservation:
    """A context-worthy fact without conversational wording."""

    kind: ObservationKind
    month: date
    as_of: date
    subcategory: str | None = None
    amount: Decimal | None = None
    percent: Decimal | None = None
    band: Decimal | None = None


@dataclass(frozen=True, slots=True)
class BudgetFundingTransfer:
    """One proposed movement from reserve/budget to a budget or reserve deficit."""

    source_kind: FundingSourceKind
    amount: Decimal
    source_subcategory: str | None = None
    target_subcategory: str | None = None


@dataclass(frozen=True, slots=True)
class BudgetChange:
    """The net proposed change to one current-month budget page."""

    subcategory: str
    budget_before: Decimal
    budget_after: Decimal


@dataclass(frozen=True, slots=True)
class OverspendResolution:
    """How much of one actual or material projected shortfall can be funded."""

    subcategory: str
    overspend: Decimal
    funded_from_variable_reserve: Decimal
    funded_from_budgets: Decimal
    unresolved: Decimal


@dataclass(frozen=True, slots=True)
class BudgetAdjustmentPlan:
    """A deterministic proposal; creating this object performs no write."""

    month: date
    as_of: date
    total_budget_before: Decimal
    total_budget_after: Decimal
    starting_variable_reserve: Decimal
    variable_spend: Decimal
    remaining_variable_reserve_before_adjustment: Decimal
    remaining_variable_reserve_after_adjustment: Decimal
    unresolved_variable_deficit: Decimal
    unresolved_budget_shortfall: Decimal
    transfers: tuple[BudgetFundingTransfer, ...]
    budget_changes: tuple[BudgetChange, ...]
    overspend_resolutions: tuple[OverspendResolution, ...]


@dataclass(frozen=True, slots=True)
class BudgetAlertDraft:
    """Structured actual-overspend alert for a future delivery integration."""

    subcategory: str
    actual_overspend: Decimal
    adjustment_amount: Decimal
    unresolved_amount: Decimal


@dataclass(frozen=True, slots=True)
class DailyBudgetAnalysis:
    """Complete result of one pure daily monitoring run."""

    status: AnalysisStatus
    month: date
    as_of: date
    monthly_income: Decimal | None
    total_budget: Decimal
    starting_variable_reserve: Decimal | None
    actual_variable_spend: Decimal
    uncategorized_spend: Decimal
    remaining_variable_reserve_before_adjustment: Decimal | None
    categories: tuple[CategoryBudgetAnalysis, ...]
    observations: tuple[BudgetObservation, ...]
    adjustment_plan: BudgetAdjustmentPlan | None
    alerts: tuple[BudgetAlertDraft, ...]


@dataclass(frozen=True, slots=True)
class DailyBudgetMonitoringReport:
    """Immutable evidence and result from one read-only monitoring run."""

    inputs: MonitoringInputSnapshot
    analysis: DailyBudgetAnalysis
