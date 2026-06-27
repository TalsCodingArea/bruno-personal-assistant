from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from agent.uncategorized_workflow import (
    async_start_uncategorized_review,
    create_uncategorized_review_graph,
    fetch_uncategorized_transactions,
)


class UncategorizedWorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def test_review_workflow_fetches_and_returns_stub_suggestions(self) -> None:
        fetched = [
            {
                "Description": "Coffee",
                "Amount": 12.5,
                "Date": "2026-06-27",
                "url": "https://notion.test/coffee",
            }
        ]
        seen_by_suggester = []

        def suggest(transactions):
            seen_by_suggester.extend(transactions)
            return transactions

        graph = create_uncategorized_review_graph(fetcher=lambda: fetched, suggester=suggest)

        state = await async_start_uncategorized_review(graph)

        self.assertEqual(state["transactions"], fetched)
        self.assertEqual(state["suggestions"], fetched)
        self.assertEqual(seen_by_suggester, fetched)
        self.assertIn("Coffee", state["messages"][-1].content)
        self.assertIn("https://notion.test/coffee", state["messages"][-1].content)

class UncategorizedFetchTest(unittest.TestCase):
    @patch.dict(os.environ, {"EXPENSES_DATABASE_ID": "expenses-db"})
    @patch("tools.notion_tools._raw_notion_response_to_dict")
    @patch("tools.notion_tools.notion_get_database_pages")
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
