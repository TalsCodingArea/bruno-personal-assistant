"""Application-wide currency policy."""

from enum import StrEnum


class Currency(StrEnum):
    """Currencies explicitly supported by this personal finance application."""

    ILS = "ILS"

    @property
    def symbol(self) -> str:
        return "₪"


DEFAULT_CURRENCY = Currency.ILS
