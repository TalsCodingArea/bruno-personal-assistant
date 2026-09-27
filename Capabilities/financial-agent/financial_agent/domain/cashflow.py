"""Cash-settlement facts derived from bank, expense, budget, income, and rule data."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from financial_agent.domain.models import BankMovement, ExpenseSettlementTotals
from financial_agent.domain.money import money


@dataclass(frozen=True, slots=True)
class AccountOutlook:
    """Projected account position after the next start-of-month settlement."""

    as_of: date
    expense_month: date
    settlement_month: date
    latest_movement: BankMovement | None
    expense_settlement: ExpenseSettlementTotals
    expected_salary: Decimal
    expected_salary_basis: str
    expected_rent: Decimal
    expected_rent_basis: str
    projected_balance_after_settlement: Decimal | None
    minimum_available_balance: Decimal | None
    available_above_minimum: Decimal | None
    on_track: bool | None
    savings_sweep_threshold: Decimal | None
    recommended_savings_transfer: Decimal | None
    missing_rules: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "expected_salary",
            "expected_rent",
            "projected_balance_after_settlement",
            "minimum_available_balance",
            "available_above_minimum",
            "savings_sweep_threshold",
            "recommended_savings_transfer",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, money(value))
