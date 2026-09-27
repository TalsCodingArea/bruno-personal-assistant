"""Jev-backed decisions for trusted notification automations."""

# ruff: noqa: RUF001 -- Hebrew Cal examples intentionally contain these characters.

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
                            "Decide whether this Cal credit-card notification reports a final "
                            "expense that should be forwarded for logging. Return the probability "
                            "of true as a Noul. Cal commonly writes a purchase in Hebrew as "
                            "'ב-<merchant> בסך<amount> בכרטיס ...': the merchant follows 'ב-' and "
                            "the numeric amount follows 'בסך', sometimes without a space and with "
                            "the currency before or after the number. This is a transaction even "
                            "when the message does not contain the words 'עסקה' or 'רכישה'. For "
                            "example, 'ב-TYPESAFE AI, INC. בסך10$ בכרטיס מסטרקארד 0273' is a "
                            "transaction. However, the phrase 'לא סופי' means the amount is a "
                            "temporary authorization/deposit and is not yet a transaction to log."
                        ),
                        criteria=NoulCriteria(
                            true=(
                                "The notification identifies both a merchant/payee and an explicit "
                                "numeric amount for a final card charge. Strong positive evidence "
                                "includes the Cal form 'ב-<merchant>' followed later by "
                                "'בסך<amount>', including compact forms such as 'בסך10$'."
                            ),
                            false=(
                                "The text contains 'לא סופי'; lacks either a merchant/payee or a "
                                "numeric amount; or reports an OTP, promotion, general account or "
                                "security notice, declined charge, refund, cancellation, or "
                                "another "
                                "non-expense event. 'לא סופי' is decisive negative evidence even "
                                "when both merchant and amount are present."
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
