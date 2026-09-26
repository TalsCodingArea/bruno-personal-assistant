"""Tests for the Notion-page to domain-object boundary."""

import asyncio
from datetime import date
from decimal import Decimal

from financial_agent.domain.models import ProgressiveMode
from financial_agent.integrations.notion_finance import NotionFinanceReader
from financial_agent.integrations.notion_schema import FinanceDataSources

from tests.fakes import FakeNotion, budget_page, expense_page


def test_reader_maps_final_to_decimal_transaction_and_scopes_tal() -> None:
    sources = FinanceDataSources("expenses", "income", "budgets", "future", "profile")
    notion = FakeNotion(
        {
            sources.expenses: [
                expense_page(
                    "expense-1",
                    description="Market",
                    date_="2026-08-03",
                    category="Food",
                    subcategory="Groceries",
                    final=50.005,
                )
            ]
        }
    )

    result = asyncio.run(
        NotionFinanceReader(notion, sources=sources).transactions(
            date(2026, 8, 1), date(2026, 8, 31)
        )
    )

    assert result[0].final_amount == Decimal("50.01")
    assert result[0].category == "Food"
    assert result[0].category_options == ("Food",)
    assert result[0].subcategory == "Groceries"
    assert {
        "property": "Tag",
        "multi_select": {"contains": "Tal 👨🏻"},
    } in notion.queries[0]["filter"]["and"]


def test_reader_resolves_one_triggered_expense_page() -> None:
    sources = FinanceDataSources("expenses", "income", "budgets", "future", "profile")
    page = expense_page(
        "expense-1",
        description="Market",
        date_="2026-08-03",
        category="Food",
        subcategory="Groceries",
        final=50,
    )
    notion = FakeNotion({sources.expenses: [page]})

    result = asyncio.run(
        NotionFinanceReader(notion, sources=sources).transaction("expense-1")
    )

    assert result is not None
    assert result.id == "expense-1"
    assert result.final_amount == Decimal("50.00")


def test_reader_preserves_multiple_categories_without_choosing_one() -> None:
    sources = FinanceDataSources("expenses", "income", "budgets", "future", "profile")
    page = expense_page(
        "expense-ambiguous",
        description="Mixed purchase",
        date_="2026-08-03",
        category="Food",
        subcategory="Groceries",
        final=50,
    )
    page["properties"]["Category"]["multi_select"].append({"name": "Home"})
    page["properties"]["Sub Category"]["multi_select"].append(
        {"name": "Household supplies"}
    )
    notion = FakeNotion({sources.expenses: [page]})

    result = asyncio.run(
        NotionFinanceReader(notion, sources=sources).transactions(
            date(2026, 8, 1), date(2026, 8, 31)
        )
    )

    assert result[0].category is None
    assert result[0].category_options == ("Food", "Home")
    assert result[0].subcategory is None
    assert result[0].subcategory_options == ("Groceries", "Household supplies")


def test_reader_maps_progressive_and_notion_percentage_volatility() -> None:
    sources = FinanceDataSources("expenses", "income", "budgets", "future", "profile")
    notion = FakeNotion(
        {
            sources.budgets: [
                budget_page(
                    "budget-1",
                    name="Groceries",
                    amount=100,
                    progressive="accumulated",
                    volatility=0.8,
                )
            ]
        }
    )

    result = asyncio.run(
        NotionFinanceReader(notion, sources=sources).budgets(date(2026, 8, 1))
    )

    assert result[0].progressive is ProgressiveMode.ACCUMULATED
    assert result[0].volatility_percent == Decimal("80.00")
    assert notion.queries[0]["filter_properties"] == ()


def test_reader_preserves_precise_volatility_and_mutation_metadata() -> None:
    sources = FinanceDataSources("expenses", "income", "budgets", "future", "profile")
    page = budget_page(
        "budget-1",
        name="Entertainment",
        amount=300,
        progressive="Accumulated",
        volatility=0.1234,
    )
    page["last_edited_time"] = "2026-08-20T04:00:00.000Z"
    page["properties"].update(
        {
            "Baseline Budget": {"type": "number", "number": 350},
            "Last Adjustment ID": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "BADJ-123"}],
            },
            "Last Adjustment Reason": {
                "type": "rich_text",
                "rich_text": [{"plain_text": "Automatic rebalance"}],
            },
            "Last Adjustment At": {
                "type": "date",
                "date": {"start": "2026-08-20T04:00:00+00:00"},
            },
        }
    )
    notion = FakeNotion({sources.budgets: [page]})

    result = asyncio.run(
        NotionFinanceReader(notion, sources=sources).budgets(date(2026, 8, 1))
    )

    assert result[0].volatility_percent == Decimal("12.34")
    assert result[0].baseline_amount == Decimal("350.00")
    assert result[0].last_adjustment_id == "BADJ-123"
    assert result[0].last_adjustment_reason == "Automatic rebalance"
    assert result[0].last_adjustment_at is not None
    assert result[0].last_edited_at is not None
