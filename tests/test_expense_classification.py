"""End-to-end behavior through the expense-classifier interface."""

import asyncio
from datetime import date
from decimal import Decimal

from financial_agent.domain.models import Transaction
from financial_agent.services.expense_classification import (
    CategoryDecision,
    ClassificationMode,
    ClassificationStage,
    ExpenseClassificationPolicy,
    ExpenseClassificationService,
    SearchEvidence,
    evaluate_history_classifier,
)

from tests.fakes import FakeFinanceReader


class FakeClassificationWriter:
    def __init__(self) -> None:
        self.applied: list[tuple[str, str, str]] = []

    async def apply_classification(
        self,
        page_id: str,
        category: str,
        subcategory: str,
    ) -> None:
        self.applied.append((page_id, category, subcategory))


class FakeCategoryDecider:
    def __init__(self, decisions: list[CategoryDecision | None]) -> None:
        self.decisions = decisions
        self.calls: list[dict[str, object]] = []

    async def decide(
        self,
        merchant: str,
        choices: tuple[object, ...],
        *,
        web_context: tuple[SearchEvidence, ...] = (),
    ) -> CategoryDecision | None:
        self.calls.append(
            {
                "merchant": merchant,
                "choices": choices,
                "web_context": web_context,
            }
        )
        return self.decisions.pop(0)


class FakeMerchantSearch:
    def __init__(self, results: tuple[SearchEvidence, ...] = ()) -> None:
        self.results = results
        self.calls: list[str] = []

    async def search(self, merchant: str) -> tuple[SearchEvidence, ...]:
        self.calls.append(merchant)
        return self.results


def transaction(
    id_: str,
    merchant: str,
    occurred_on: date,
    *,
    category: str | None = None,
    subcategory: str | None = None,
) -> Transaction:
    return Transaction(
        id=id_,
        description=merchant,
        occurred_on=occurred_on,
        final_amount=Decimal("20.00"),
        category=category,
        subcategory=subcategory,
    )


def policy(mode: ClassificationMode = ClassificationMode.APPLY) -> ExpenseClassificationPolicy:
    return ExpenseClassificationPolicy(
        mode=mode,
        confidence_threshold=Decimal("0.80"),
    )


def test_history_evaluation_walks_forward_and_reports_coverage() -> None:
    rows = (
        transaction(
            "1",
            "Known Market 100",
            date(2026, 1, 1),
            category="Home",
            subcategory="Groceries",
        ),
        transaction(
            "2",
            "Known Cafe",
            date(2026, 1, 2),
            category="Food",
            subcategory="Restaurants",
        ),
        transaction(
            "3",
            "KNOWN MARKET 200",
            date(2026, 2, 1),
            category="Home",
            subcategory="Groceries",
        ),
        transaction(
            "4",
            "One-off Pharmacy",
            date(2026, 2, 2),
            category="Health",
            subcategory="Pharmacy",
        ),
    )

    result = evaluate_history_classifier(rows, policy=policy())

    assert result.eligible == 4
    assert result.classified == 1
    assert result.correct == 1
    assert result.coverage == Decimal("0.2500")
    assert result.accuracy == Decimal("1.0000")


def test_high_confidence_history_match_skips_models_and_search() -> None:
    async def scenario() -> None:
        current = transaction("current", "SUPER-PHARM #4567", date(2026, 9, 27))
        history = transaction(
            "past",
            "Super Pharm 1234",
            date(2026, 8, 1),
            category="Health",
            subcategory="Pharmacy",
        )
        writer = FakeClassificationWriter()
        decider = FakeCategoryDecider([])
        search = FakeMerchantSearch()
        classifier = ExpenseClassificationService(
            FakeFinanceReader(transactions=(current, history)),
            writer,
            decider=decider,
            search=search,
            policy=policy(),
        )

        result = await classifier.classify_created_expense(
            "current", as_of=date(2026, 9, 27)
        )

        assert result.stage is ClassificationStage.HISTORY
        assert result.category == "Health"
        assert result.subcategory == "Pharmacy"
        assert result.applied is True
        assert writer.applied == [("current", "Health", "Pharmacy")]
        assert decider.calls == []
        assert search.calls == []

    asyncio.run(scenario())


def test_merchant_decision_skips_web_when_confident() -> None:
    async def scenario() -> None:
        current = transaction("current", "Blue Rocket", date(2026, 9, 27))
        history = transaction(
            "past",
            "Known Cafe",
            date(2026, 8, 1),
            category="Food",
            subcategory="Restaurants",
        )
        writer = FakeClassificationWriter()
        decider = FakeCategoryDecider(
            [
                CategoryDecision(
                    category="Food",
                    subcategory="Restaurants",
                    confidence=Decimal("0.88"),
                    reason="Merchant name indicates a cafe.",
                )
            ]
        )
        search = FakeMerchantSearch()
        classifier = ExpenseClassificationService(
            FakeFinanceReader(transactions=(current, history)),
            writer,
            decider=decider,
            search=search,
            policy=policy(),
        )

        result = await classifier.classify_created_expense(
            "current", as_of=date(2026, 9, 27)
        )

        assert result.stage is ClassificationStage.MERCHANT
        assert result.applied is True
        assert search.calls == []

    asyncio.run(scenario())


def test_low_confidence_merchant_uses_web_then_applies() -> None:
    async def scenario() -> None:
        current = transaction("current", "Mystery Ltd", date(2026, 9, 27))
        history = transaction(
            "past",
            "Known Market",
            date(2026, 8, 1),
            category="Home",
            subcategory="Groceries",
        )
        writer = FakeClassificationWriter()
        decider = FakeCategoryDecider(
            [
                CategoryDecision(
                    category="Home",
                    subcategory="Groceries",
                    confidence=Decimal("0.55"),
                    reason="Weak merchant-name inference.",
                ),
                CategoryDecision(
                    category="Home",
                    subcategory="Groceries",
                    confidence=Decimal("0.91"),
                    reason="Search identifies a grocery store.",
                ),
            ]
        )
        search = FakeMerchantSearch(
            (
                SearchEvidence(
                    title="Mystery Ltd grocery delivery",
                    url="https://example.com/mystery",
                    content="An Israeli grocery delivery merchant.",
                ),
            )
        )
        classifier = ExpenseClassificationService(
            FakeFinanceReader(transactions=(current, history)),
            writer,
            decider=decider,
            search=search,
            policy=policy(),
        )

        result = await classifier.classify_created_expense(
            "current", as_of=date(2026, 9, 27)
        )

        assert result.stage is ClassificationStage.WEB
        assert result.confidence == Decimal("0.91")
        assert result.applied is True
        assert search.calls == ["Mystery Ltd"]
        assert decider.calls[0]["web_context"] == ()
        assert decider.calls[1]["web_context"] == search.results

    asyncio.run(scenario())


def test_low_confidence_after_web_stays_uncategorized() -> None:
    async def scenario() -> None:
        current = transaction("current", "Mystery Ltd", date(2026, 9, 27))
        history = transaction(
            "past",
            "Known Market",
            date(2026, 8, 1),
            category="Home",
            subcategory="Groceries",
        )
        writer = FakeClassificationWriter()
        decider = FakeCategoryDecider(
            [
                CategoryDecision(
                    "Home", "Groceries", Decimal("0.50"), "Weak guess."
                ),
                CategoryDecision(
                    "Home", "Groceries", Decimal("0.70"), "Still uncertain."
                ),
            ]
        )
        search = FakeMerchantSearch(
            (SearchEvidence("Result", "https://example.com", "Ambiguous business."),)
        )
        classifier = ExpenseClassificationService(
            FakeFinanceReader(transactions=(current, history)),
            writer,
            decider=decider,
            search=search,
            policy=policy(),
        )

        result = await classifier.classify_created_expense(
            "current", as_of=date(2026, 9, 27)
        )

        assert result.stage is ClassificationStage.UNRESOLVED
        assert result.applied is False
        assert writer.applied == []

    asyncio.run(scenario())


def test_shadow_mode_returns_decision_without_writing() -> None:
    async def scenario() -> None:
        current = transaction("current", "Super Pharm", date(2026, 9, 27))
        history = transaction(
            "past",
            "Super Pharm",
            date(2026, 8, 1),
            category="Health",
            subcategory="Pharmacy",
        )
        writer = FakeClassificationWriter()
        classifier = ExpenseClassificationService(
            FakeFinanceReader(transactions=(current, history)),
            writer,
            decider=None,
            search=None,
            policy=policy(ClassificationMode.SHADOW),
        )

        result = await classifier.classify_created_expense(
            "current", as_of=date(2026, 9, 27)
        )

        assert result.stage is ClassificationStage.HISTORY
        assert result.applied is False
        assert result.would_apply is True
        assert writer.applied == []

    asyncio.run(scenario())
