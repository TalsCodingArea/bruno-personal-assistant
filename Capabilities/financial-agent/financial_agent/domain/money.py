"""Decimal-safe currency primitives."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Decimal | int | str) -> Decimal:
    """Create a two-decimal currency amount without passing through binary floats."""

    if isinstance(value, float):
        raise TypeError("Currency values must not be constructed from float")
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def money_from_api(value: object) -> Decimal:
    """Convert a JSON scalar to Decimal through its base-10 representation."""

    if isinstance(value, bool) or not isinstance(value, int | float | str | Decimal):
        raise TypeError(f"Unsupported money value: {value!r}")
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
