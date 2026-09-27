"""Jev notification classification policy."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from financial_agent.integrations.jev import JevTransactionClassifier


class FakeJevRunnable:
    def __init__(self, probability: float) -> None:
        self.probability = probability
        self.requests: list[dict[str, Any]] = []

    async def ainvoke(self, request: dict[str, Any]) -> Any:
        self.requests.append(request)
        return SimpleNamespace(
            nouls={
                "is_expense_transaction": SimpleNamespace(noul=self.probability)
            },
            model="jev-1.13.0",
            request_id="request-1",
        )


def test_jev_requires_amount_and_merchant_and_applies_configured_threshold() -> None:
    async def scenario() -> None:
        runnable = FakeJevRunnable(0.79)
        with patch(
            "financial_agent.integrations.jev.TypeSafeClassifier",
            return_value=runnable,
        ):
            classifier = JevTransactionClassifier(
                api_key="test-key",
                threshold=0.8,
            )

        below = await classifier.classify_notification("Cal notification")
        runnable.probability = 0.81
        above = await classifier.classify_notification("Cal transaction")

        question = runnable.requests[0]["questions"]["is_expense_transaction"]
        assert "amount" in question.instructions
        assert "merchant" in question.instructions
        assert below.is_transaction is False
        assert above.is_transaction is True
        assert above.model == "jev-1.13.0"

    asyncio.run(scenario())


def test_cal_purchase_structure_is_explained_to_jev_and_thresholded() -> None:
    async def scenario() -> None:
        runnable = FakeJevRunnable(0.95)
        with patch(
            "financial_agent.integrations.jev.TypeSafeClassifier",
            return_value=runnable,
        ):
            classifier = JevTransactionClassifier(api_key="test-key")

        decision = await classifier.classify_notification(
            "ב-TYPESAFE AI, INC. בסך10$ בכרטיס מסטרקארד 0273, "  # noqa: RUF001
            "פרטים נוספים בעוד רגע באפליקציה"
        )

        assert decision.is_transaction is True
        assert len(runnable.requests) == 1
        request = runnable.requests[0]
        assert request["state"]["notification"].startswith("ב-TYPESAFE")
        question = request["questions"]["is_expense_transaction"]
        assert "ב-<merchant>" in question.instructions
        assert "בסך10$" in question.instructions  # noqa: RUF001
        assert "לא סופי" in question.instructions

    asyncio.run(scenario())


def test_cal_non_final_amount_is_not_a_transaction() -> None:
    async def scenario() -> None:
        runnable = FakeJevRunnable(0.12)
        with patch(
            "financial_agent.integrations.jev.TypeSafeClassifier",
            return_value=runnable,
        ):
            classifier = JevTransactionClassifier(api_key="test-key")

        decision = await classifier.classify_notification(
            "ב-HOTEL EXAMPLE בסך 500 ש״ח לא סופי בכרטיס מסטרקארד 0273"
        )

        assert decision.is_transaction is False
        assert len(runnable.requests) == 1
        question = runnable.requests[0]["questions"]["is_expense_transaction"]
        assert "לא סופי" in question.criteria.false
        assert "decisive negative evidence" in question.criteria.false

    asyncio.run(scenario())
