"""Jev-backed decisions for trusted notification automations."""

from dataclasses import dataclass

from langchain_typesafe import Noul, NoulCriteria, TypeSafeClassifier
from pydantic import SecretStr


@dataclass(frozen=True, slots=True)
class TransactionNotificationDecision:
    """Whether a phone notification is safe to pass to expense extraction."""

    is_transaction: bool
    probability: float
    model: str
    request_id: str | None


class JevTransactionClassifier:
    """Classify Cal notifications with Jev's typed binary decision API."""

    def __init__(
        self,
        *,
        api_key: SecretStr | str,
        model: str = "jev-latest",
        threshold: float = 0.8,
    ) -> None:
        if not 0 < threshold <= 1:
            raise ValueError("Jev transaction threshold must be greater than 0 and at most 1")
        self._classifier = TypeSafeClassifier(api_key=api_key, model=model)
        self._threshold = threshold

    async def classify_notification(self, text: str) -> TransactionNotificationDecision:
        """Return a thresholded Jev decision without performing any side effect."""

        message = text.strip()
        if not message:
            raise ValueError("notification text cannot be empty")
        response = await self._classifier.ainvoke(
            {
                "state": {
                    "source": "Cal credit-card application phone notification",
                    "notification": message,
                },
                "questions": {
                    "is_expense_transaction": Noul(
                        instructions=(
                            "Does this notification report an actual card expense that should "
                            "be logged? An actual transaction must identify both a numeric "
                            "amount and a merchant or payee name."
                        ),
                        criteria=NoulCriteria(
                            true=(
                                "A completed or pending purchase/charge with an explicit "
                                "numeric amount and an identifiable merchant or payee."
                            ),
                            false=(
                                "Missing either amount or merchant/payee, or it is an OTP, "
                                "promotion, general account/security notice, declined charge, "
                                "refund, cancellation, or another non-expense event."
                            ),
                        ),
                    )
                },
            }
        )
        probability = response.nouls["is_expense_transaction"].noul
        return TransactionNotificationDecision(
            is_transaction=probability >= self._threshold,
            probability=probability,
            model=response.model,
            request_id=response.request_id,
        )
