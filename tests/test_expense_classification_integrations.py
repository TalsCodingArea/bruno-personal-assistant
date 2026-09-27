"""Contract tests for classifier provider adapters."""

import asyncio
from types import SimpleNamespace
from typing import Any

from financial_agent.integrations.jev_category_decider import JevCategoryDecider
from financial_agent.integrations.tavily_search import TavilyMerchantSearch
from financial_agent.services.expense_classification import (
    CategoryChoice,
    SearchEvidence,
)


class FakeJevChoiceClassifier:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def ainvoke(self, request: dict[str, Any]) -> Any:
        self.requests.append(request)
        return SimpleNamespace(
            choices={
                "category_pair": SimpleNamespace(
                    choice="category_1",
                    probabilities={"category_0": 0.08, "category_1": 0.92},
                    confidence=0.87,
                )
            }
        )


class FakeTavilyClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((query, kwargs))
        return {
            "results": [
                {
                    "title": "Merchant profile",
                    "url": "https://example.com/merchant",
                    "content": "Grocery delivery in Israel." * 100,
                },
                {"title": None, "url": "https://invalid", "content": "ignored"},
            ]
        }


def test_jev_decider_maps_choice_back_to_existing_pair() -> None:
    async def scenario() -> None:
        classifier = FakeJevChoiceClassifier()
        decider = JevCategoryDecider(
            api_key="test-key",
            classifier=classifier,
        )
        choices = (
            CategoryChoice("Home", "Groceries", ("Market",), 4),
            CategoryChoice("Food", "Restaurants", ("Cafe",), 3),
        )

        decision = await decider.decide(
            "New Cafe",
            choices,
            web_context=(
                SearchEvidence(
                    "New Cafe profile",
                    "https://example.com/new-cafe",
                    "A neighborhood restaurant.",
                ),
            ),
        )

        assert decision is not None
        assert (decision.category, decision.subcategory) == (
            "Food",
            "Restaurants",
        )
        assert str(decision.confidence) == "0.87"
        request = classifier.requests[0]
        assert request["state"]["merchant"] == "New Cafe"
        assert request["state"]["public_web_evidence"][0]["title"] == (
            "New Cafe profile"
        )
        criteria = request["questions"]["category_pair"].criteria
        assert set(criteria) == {"category_0", "category_1"}

    asyncio.run(scenario())


def test_tavily_search_sends_only_merchant_context_and_bounds_results() -> None:
    async def scenario() -> None:
        client = FakeTavilyClient()
        search = TavilyMerchantSearch("test-key", client=client)

        evidence = await search.search("  Mystery   Market  ")

        query, options = client.calls[0]
        assert query == '"Mystery Market" Israel business merchant category'
        assert options["search_depth"] == "basic"
        assert options["max_results"] == 3
        assert len(evidence) == 1
        assert len(evidence[0].content) == 1_000

    asyncio.run(scenario())
