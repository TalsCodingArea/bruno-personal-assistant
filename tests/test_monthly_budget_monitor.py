from __future__ import annotations

import unittest
from datetime import date

from tools.monthly_budget.budget_forcaster import ForecastResult
from tools.monthly_budget.budget_monitor import _append_logged_expense_if_missing, decide_budget_alert
from tools.monthly_budget.models import Month
from tools.monthly_budget.notion_writer import BudgetPage


class MonthlyBudgetMonitorTest(unittest.TestCase):
    def test_over_budget_alert_requests_budget_update(self) -> None:
        alert = decide_budget_alert(
            BudgetPage("Groceries 🛒", 500, date(2026, 5, 1), "page-id"),
            ForecastResult(
                sub_category="Groceries 🛒",
                classification="predictable_variable",
                actual_so_far=650,
                historical_average=900,
                projected_month_total=1100,
                confidence="high",
                reason="test",
            ),
            Month(2026, 5),
            as_of=date(2026, 5, 17),
        )

        self.assertIsNotNone(alert)
        self.assertEqual(alert.severity, "over_budget")
        self.assertTrue(alert.updated)
        self.assertEqual(alert.budget_after, 650)

    def test_predictable_critical_projection_alerts_without_update(self) -> None:
        alert = decide_budget_alert(
            BudgetPage("Takeout 🥡", 500, date(2026, 5, 1), "page-id"),
            ForecastResult(
                sub_category="Takeout 🥡",
                classification="predictable_variable",
                actual_so_far=450,
                historical_average=800,
                projected_month_total=900,
                confidence="high",
                reason="test",
            ),
            Month(2026, 5),
            as_of=date(2026, 5, 8),
        )

        self.assertIsNotNone(alert)
        self.assertEqual(alert.severity, "critical_projection")
        self.assertFalse(alert.updated)

    def test_non_predictable_projection_does_not_alert_before_over_budget(self) -> None:
        alert = decide_budget_alert(
            BudgetPage("Gift 🎁", 500, date(2026, 5, 1), "page-id"),
            ForecastResult(
                sub_category="Gift 🎁",
                classification="non_predictable",
                actual_so_far=450,
                historical_average=800,
                projected_month_total=900,
                confidence="medium",
                reason="test",
            ),
            Month(2026, 5),
            as_of=date(2026, 5, 8),
        )

        self.assertIsNone(alert)

    def test_logged_expense_is_appended_when_notion_query_has_not_caught_up(self) -> None:
        records = _append_logged_expense_if_missing(
            [],
            {
                "Description": "Coffee",
                "Amount": 20,
                "Date": "2026-05-17",
                "Sub Category": ["Snacks & Drinks 🍫"],
            },
            ["Snacks & Drinks 🍫"],
            date(2026, 5, 17),
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["Amount"], 20)
        self.assertEqual(records[0]["Sub Category"], ["Snacks & Drinks 🍫"])


if __name__ == "__main__":
    unittest.main()
