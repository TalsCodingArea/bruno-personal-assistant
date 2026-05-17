from __future__ import annotations

import unittest
from datetime import date

from tools.monthly_budget.models import Month
from tools.monthly_budget.notion_writer import (
    archive_budget_page,
    build_budget_page_properties,
    find_budget_page_by_name,
    upsert_budget_page,
    upsert_monthly_budget_pages_from_preview,
    update_budget_page_amount,
)


class FakeDatabases:
    def __init__(self, pages):
        self.pages = pages
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        query_filter = kwargs.get("filter", {})
        if query_filter == {"property": "Date", "date": {"equals": "2026-05-01"}}:
            return {
                "results": [{"id": "summary-page", "url": "https://notion.test/summary"}],
                "has_more": False,
            }
        return {"results": self.pages, "has_more": False}


class FakePages:
    def __init__(self):
        self.updated = []
        self.created = []

    def update(self, **kwargs):
        self.updated.append(kwargs)
        return {"id": kwargs["page_id"], "url": "https://notion.test/existing"}

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "new-page", "url": "https://notion.test/new"}


class FakeClient:
    def __init__(self, pages=None):
        self.databases = FakeDatabases(pages or [])
        self.pages = FakePages()


class MonthlyBudgetNotionWriterTest(unittest.TestCase):
    def test_build_budget_page_properties(self) -> None:
        props = build_budget_page_properties(
            "Restaurant 🍷",
            250.567,
            Month(2026, 5),
            "summary-page-id",
        )

        self.assertEqual(props["Name"]["title"][0]["text"]["content"], "Restaurant 🍷")
        self.assertEqual(props["Budget"]["number"], 250.57)
        self.assertEqual(props["Date"]["date"]["start"], "2026-05-01")
        self.assertEqual(props["Financial Summary"]["relation"][0]["id"], "summary-page-id")

    def test_upsert_budget_page_dry_run_reports_create_without_writing(self) -> None:
        client = FakeClient()
        result = upsert_budget_page(
            "Groceries 🛒",
            1200,
            Month(2026, 5),
            "summary-page-id",
            dry_run=True,
            client=client,
            database_id="budget-db",
        )

        self.assertEqual(result.action, "create")
        self.assertEqual(result.date, date(2026, 5, 1))
        self.assertEqual(client.pages.created, [])
        self.assertEqual(client.pages.updated, [])

    def test_upsert_budget_page_updates_existing_when_apply_enabled(self) -> None:
        client = FakeClient(pages=[{"id": "existing-page", "url": "https://notion.test/existing"}])
        result = upsert_budget_page(
            "Groceries 🛒",
            1200,
            Month(2026, 5),
            "summary-page-id",
            dry_run=False,
            client=client,
            database_id="budget-db",
        )

        self.assertEqual(result.action, "updated")
        self.assertEqual(result.page_id, "existing-page")
        self.assertEqual(len(client.pages.updated), 1)
        self.assertEqual(client.pages.created, [])

    def test_find_budget_page_by_name_matches_without_emoji(self) -> None:
        client = FakeClient(
            pages=[
                {
                    "id": "groceries-page",
                    "url": "https://notion.test/groceries",
                    "properties": {
                        "Name": {"title": [{"plain_text": "Groceries 🛒"}]},
                        "Budget": {"number": 1200},
                        "Date": {"date": {"start": "2026-05-01"}},
                    },
                }
            ]
        )

        result = find_budget_page_by_name(
            "groceries",
            Month(2026, 5),
            client=client,
            database_id="budget-db",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.sub_category, "Groceries 🛒")

    def test_update_budget_page_amount_updates_budget_only(self) -> None:
        client = FakeClient(
            pages=[
                {
                    "id": "groceries-page",
                    "url": "https://notion.test/groceries",
                    "properties": {
                        "Name": {"title": [{"plain_text": "Groceries 🛒"}]},
                        "Budget": {"number": 1200},
                        "Date": {"date": {"start": "2026-05-01"}},
                    },
                }
            ]
        )

        result = update_budget_page_amount(
            "groceries",
            900,
            Month(2026, 5),
            client=client,
            database_id="budget-db",
        )

        self.assertEqual(result.sub_category, "Groceries 🛒")
        self.assertEqual(result.budget, 900)
        self.assertEqual(client.pages.updated[0]["properties"], {"Budget": {"number": 900.0}})

    def test_archive_budget_page_archives_existing_page(self) -> None:
        client = FakeClient(
            pages=[
                {
                    "id": "gift-page",
                    "url": "https://notion.test/gift",
                    "properties": {
                        "Name": {"title": [{"plain_text": "Gift 🎁"}]},
                        "Budget": {"number": 500},
                        "Date": {"date": {"start": "2026-05-01"}},
                    },
                }
            ]
        )

        result = archive_budget_page(
            "gift",
            Month(2026, 5),
            client=client,
            database_id="budget-db",
        )

        self.assertEqual(result.action, "archived")
        self.assertEqual(client.pages.updated[0]["archived"], True)

    def test_monthly_upsert_skips_current_month_only_subcategories(self) -> None:
        client = FakeClient()
        preview = {
            "target_month": {"year": 2026, "month": 5},
            "classifications": {
                "Groceries 🛒": {"kind": "predictable_variable"},
                "Gift 🎁": {"kind": "non_predictable"},
            },
            "allocations": {
                "Groceries 🛒": {"budget": 1200},
                "Gift 🎁": {"budget": 500},
                "Vacation 🏖️": {"budget": 900},
            },
        }

        result = upsert_monthly_budget_pages_from_preview(preview, dry_run=True, client=client)

        self.assertEqual(len(result["writes"]), 1)
        self.assertEqual(result["writes"][0]["sub_category"], "Groceries 🛒")

    def test_monthly_upsert_can_include_non_predictable_when_explicit(self) -> None:
        client = FakeClient()
        preview = {
            "target_month": {"year": 2026, "month": 5},
            "classifications": {
                "Groceries 🛒": {"kind": "predictable_variable"},
                "Gift 🎁": {"kind": "non_predictable"},
            },
            "allocations": {
                "Groceries 🛒": {"budget": 1200},
                "Gift 🎁": {"budget": 500},
            },
        }

        result = upsert_monthly_budget_pages_from_preview(
            preview,
            dry_run=True,
            include_non_predictable=True,
            client=client,
        )

        self.assertEqual([write["sub_category"] for write in result["writes"]], ["Groceries 🛒", "Gift 🎁"])


if __name__ == "__main__":
    unittest.main()
