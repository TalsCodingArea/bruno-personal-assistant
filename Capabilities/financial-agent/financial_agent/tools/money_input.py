"""OpenAI-compatible currency inputs for agent-facing tools."""

from decimal import Decimal, DecimalException
from typing import Annotated

from pydantic import Field

from financial_agent.domain.money import ZERO, money

CurrencyText = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        description=(
            "Base-10 currency amount as a string without a currency symbol or "
            "thousands separators, for example '2000.00'."
        ),
    ),
]


def parse_currency_text(
    value: str,
    *,
    field_name: str,
    non_negative: bool = False,
    positive: bool = False,
) -> Decimal:
    """Convert a tool-boundary string to validated Decimal-safe currency."""

    try:
        amount = money(value)
    except (DecimalException, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid currency amount") from exc
    if not amount.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if positive and amount <= ZERO:
        raise ValueError(f"{field_name} must be positive")
    if non_negative and amount < ZERO:
        raise ValueError(f"{field_name} cannot be negative")
    return amount
