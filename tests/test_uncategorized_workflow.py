from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from personal_assistant.agent.general.uncategorized_workflow import (
    async_start_uncategorized_review,
    create_uncategorized_review_graph,
    fetch_uncategorized_transactions,
)


class UncategorizedWorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def test_review_workflow_fetches_suggests_and_formats(self) -> None:
        fetched = [
            {
                "id": "page-1",
                "Description": "Coffee",
                "Amount": 12.5,
                "Date": "2026-06-27",
                "url": "https://notion.test/coffee",
            }
        ]
        suggestions = [
            {
                "review_id": "abc123",
                "description": "Coffee",
                "amount": 12.5,
                "date": "2026-06-27",
                "predicted_category": "Lifestyle 🏞️",
                "predicted_sub_category": "Snacks & Drinks 🍫",
                "confidence": 0.87,
            }
        ]
        seen_by_suggester = []

        def suggest(transactions):
            seen_by_suggester.extend(transactions)
            return suggestions

        graph = create_uncategorized_review_graph(fetcher=lambda: fetched, suggester=suggest)

        state = await async_start_uncategorized_review(graph)

        self.assertEqual(state["transactions"], fetched)
        self.assertEqual(state["suggestions"], suggestions)
        self.assertEqual(seen_by_suggester, fetched)
        message = state["messages"][-1].content
        self.assertIn("Coffee", message)
        self.assertIn("abc123", message)
        self.assertIn("Snacks & Drinks 🍫", message)
        self.assertIn("87%", message)

    async def test_review_workflow_shows_unpredicted_items(self) -> None:
        suggestions = [
            {
                "review_id": "def456",
                "description": "Mystery",
                "amount": 10,
                "date": "2026-07-01",
                "predicted_category": None,
                "predicted_sub_category": None,
                "confidence": None,
            }
        ]
        graph = create_uncategorized_review_graph(fetcher=lambda: [], suggester=lambda t: suggestions)

        state = await async_start_uncategorized_review(graph)

        self.assertIn("no prediction", state["messages"][-1].content)

class UncategorizedStatusToolTest(unittest.TestCase):
    """get_uncategorized_expenses_status is a plain tool: pull + brief, no workflow."""

    @patch("personal_assistant.tools.expense_review_tools.sync_uncategorized_to_review_queue")
    @patch("personal_assistant.tools.expense_review_tools.fetch_uncategorized_expenses")
    def test_status_reports_counts_only(self, fetch, sync) -> None:
        from personal_assistant.tools.expense_review_tools import get_uncategorized_expenses_status

        fetch.return_value = [{"id": f"page-{i}"} for i in range(3)]
        sync.return_value = [
            {"review_id": "r1", "predicted_sub_category": "Supermarket 🛒"},
            {"review_id": "r2", "predicted_sub_category": "Fuel ⛽"},
            {"review_id": "r3", "predicted_sub_category": None},
        ]

        result = get_uncategorized_expenses_status.invoke({})

        self.assertIn("Uncategorized expenses in Notion: 3", result)
        self.assertIn("With a queued ML suggestion: 2", result)
        self.assertIn("without a prediction yet", result)
        # A status brief must not drag in summaries or projections.
        self.assertNotIn("projected", result.lower())
        self.assertNotIn("total spent", result.lower())

    @patch("personal_assistant.tools.expense_review_tools.sync_uncategorized_to_review_queue")
    @patch("personal_assistant.tools.expense_review_tools.fetch_uncategorized_expenses")
    def test_status_when_clean(self, fetch, sync) -> None:
        from personal_assistant.tools.expense_review_tools import get_uncategorized_expenses_status

        fetch.return_value = []
        sync.return_value = []

        result = get_uncategorized_expenses_status.invoke({})

        self.assertIn("No uncategorized expenses", result)


class UncategorizedFetchTest(unittest.TestCase):
    @patch.dict(os.environ, {"EXPENSES_DATABASE_ID": "expenses-db"})
    @patch("personal_assistant.tools.notion_tools._raw_notion_response_to_dict")
    @patch("personal_assistant.tools.notion_tools.notion_get_database_pages")
    def test_fetch_uncategorized_transactions_uses_uncategorized_and_tal_filter(
        self,
        notion_get_database_pages,
        raw_to_dict,
    ) -> None:
        notion_get_database_pages.invoke.return_value = {"results": []}
        raw_to_dict.return_value = [
            {
                "Description": "Coffee",
                "Final": 12.5,
                "Amount": None,
                "Category": ["Uncategorized"],
                "Sub Category": [],
                "Date": "2026-06-27",
                "url": "https://notion.test/coffee",
            }
        ]

        rows = fetch_uncategorized_transactions()

        query = notion_get_database_pages.invoke.call_args.args[0]
        self.assertEqual(query["database_id"], "expenses-db")
        self.assertIn(
            {"property": "Category", "multi_select": {"contains": "Uncategorized"}},
            query["filter"]["and"],
        )
        self.assertIn(
            {"property": "Tag", "multi_select": {"contains": "Tal 👨🏻"}},
            query["filter"]["and"],
        )
        self.assertEqual(rows[0]["Amount"], 12.5)
        self.assertNotIn("Final", rows[0])


if __name__ == "__main__":
    unittest.main()
