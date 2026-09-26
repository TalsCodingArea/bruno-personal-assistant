"""Domain values for bank-account movements."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class TransactionDirection(StrEnum):
    """The direction of a bank movement from the account's perspective."""

    POSITIVE = "Positive"
    NEGATIVE = "Negative"


@dataclass(frozen=True, slots=True)
class BankTransaction:
    """One normalized transaction ready for the Notion data source."""

    fingerprint: str
    uid: str
    title: str
    occurred_on: date
    direction: TransactionDirection
    amount: Decimal
    balance_after: Decimal
    description: str
    action: str

    def visible_key(self) -> tuple[str, ...]:
        """Return the exact user-visible fields used for remote deduplication."""

        return (
            self.title,
            self.occurred_on.isoformat(),
            self.direction.value,
            str(self.amount),
            str(self.balance_after),
            self.description,
            self.action,
        )
