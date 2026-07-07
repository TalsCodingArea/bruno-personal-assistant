from __future__ import annotations

import unittest
from datetime import date

from personal_assistant.tools.monthly_budget import (
    Month,
    calculate_budget_allocations,
    classify_expenses,
    evaluate_budget_status,
    forecast_monthly_spend,
    predict_monthly_income,
)


def expense(day: date, amount: float, sub_category: str) -> dict:
    return {
        "Date": day.isoformat(),
        "Amount": amount,
        "Sub Category": [sub_category],
        "Category": ["Lifestyle"],
        "Description": sub_category,
    }


def income(day: date, amount: float) -> dict:
    return {
        "Date": day.isoformat(),
        "Amount": amount,
        "Description": "Salary",
    }


class MonthlyBudgetEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.target_month = Month(2026, 5)
        self.expenses = []

        for month in (1, 2, 3, 4):
            self.expenses.append(expense(date(2026, month, 3), 220, "Gym Membership"))
            for day in (2, 8, 14, 21, 27):
                self.expenses.append(expense(date(2026, month, day), 120 + month, "Groceries"))
            self.expenses.append(expense(date(2026, month, 16), 350 + (month * 20), "Restaurants"))

        self.expenses.extend(
            [
                expense(date(2026, 2, 10), 900, "Home Goods"),
                expense(date(2026, 4, 22), 1200, "Home Goods"),
                expense(date(2026, 5, 3), 220, "Gym Membership"),
                expense(date(2026, 5, 5), 180, "Groceries"),
                expense(date(2026, 5, 9), 190, "Groceries"),
                expense(date(2026, 5, 10), 800, "Restaurants"),
            ]
        )

        self.income = [
            income(date(2026, 1, 10), 12000),
            income(date(2026, 2, 10), 12100),
            income(date(2026, 3, 10), 11900),
            income(date(2026, 4, 10), 12200),
        ]

    def test_predicts_income_before_current_month_income_is_logged(self) -> None:
        prediction = predict_monthly_income(self.income, self.target_month)

        self.assertEqual(prediction.current_month_income, 0)
        self.assertGreater(prediction.predicted_income, 11900)
        self.assertEqual(prediction.confidence, "high")

    def test_classifies_recurring_predictable_and_non_predictable_expenses(self) -> None:
        classifications = classify_expenses(self.expenses, self.target_month, lookback_months=4)

        self.assertEqual(classifications["Gym Membership"].kind, "recurring")
        self.assertEqual(classifications["Groceries"].kind, "predictable_variable")
        self.assertEqual(classifications["Home Goods"].kind, "non_predictable")

    def test_forecast_does_not_over_extrapolate_non_predictable_expenses(self) -> None:
        classifications = classify_expenses(self.expenses, self.target_month, lookback_months=4)
        forecasts = forecast_monthly_spend(
            self.expenses,
            classifications,
            self.target_month,
            as_of=date(2026, 5, 10),
        )

        self.assertGreaterEqual(forecasts["Gym Membership"].projected_month_total, 220)
        self.assertGreater(forecasts["Groceries"].projected_month_total, forecasts["Groceries"].actual_so_far)
        self.assertLess(
            forecasts["Home Goods"].projected_month_total,
            forecasts["Restaurants"].projected_month_total,
        )

    def test_budget_allocation_funds_predictable_categories_then_variable_pool(self) -> None:
        prediction = predict_monthly_income(self.income, self.target_month)
        classifications = classify_expenses(self.expenses, self.target_month, lookback_months=4)
        forecasts = forecast_monthly_spend(
            self.expenses,
            classifications,
            self.target_month,
            as_of=date(2026, 5, 10),
        )
        allocations = calculate_budget_allocations(prediction, forecasts)

        self.assertIn("Gym Membership", allocations)
        self.assertIn("Groceries", allocations)
        self.assertIn("Home Goods", allocations)
        self.assertGreater(allocations["Groceries"].budget, allocations["Gym Membership"].budget)
        self.assertGreater(allocations["Home Goods"].variable_pool_share, 0)

    def test_budget_status_notifies_only_for_real_deviation_or_over_budget(self) -> None:
        classifications = classify_expenses(self.expenses, self.target_month, lookback_months=4)
        forecasts = forecast_monthly_spend(
            self.expenses,
            classifications,
            self.target_month,
            as_of=date(2026, 5, 10),
        )

        normal = evaluate_budget_status(
            "Groceries",
            1200,
            forecasts["Groceries"],
            self.target_month,
            as_of=date(2026, 5, 10),
        )
        off_track = evaluate_budget_status(
            "Restaurants",
            900,
            forecasts["Restaurants"],
            self.target_month,
            as_of=date(2026, 5, 10),
        )
        over_budget = evaluate_budget_status(
            "Restaurants",
            500,
            forecasts["Restaurants"],
            self.target_month,
            as_of=date(2026, 5, 10),
        )

        self.assertFalse(normal.should_notify)
        self.assertTrue(off_track.should_notify)
        self.assertEqual(off_track.severity, "off_track")
        self.assertTrue(over_budget.should_notify)
        self.assertEqual(over_budget.severity, "over_budget")


if __name__ == "__main__":
    unittest.main()
