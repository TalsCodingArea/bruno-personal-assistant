from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

AffordabilityLevel = Literal[
    "affordable_now",
    "affordable_with_plan",
    "not_recommended",
    "needs_more_info",
]


@dataclass(frozen=True)
class EmergencyFundResult:
    target: float
    balance: float
    gap: float
    surplus: float
    required_months: float
    status: Literal["below_target", "at_or_above_target"]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FutureExpenseReserve:
    amount: float
    months_remaining: int
    monthly_reserve: float
    due_date: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AffordabilityResult:
    level: AffordabilityLevel
    estimated_cost: float | None
    emergency_target: float | None
    latest_balance: float | None
    available_after_emergency: float | None
    month_remaining_budget: float | None
    required_reserves: float
    saving_months: int | None
    reasons: list[str]
    missing: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
