from __future__ import annotations

import unittest
from datetime import date

from tools.financial_advisor.engine import (
    calculate_available_surplus,
    calculate_emergency_fund_target,
    calculate_future_expense_reserve,
    evaluate_desire_affordability,
    evaluate_emergency_fund,
    score_desire,
)


class FinancialAdvisorEngineTest(unittest.TestCase):
    def test_emergency_fund_target_defaults_to_three_months(self) -> None:
        self.assertEqual(calculate_emergency_fund_target(10000), 30000)
        self.assertEqual(calculate_emergency_fund_target(10000, months=4), 40000)

    def test_evaluates_emergency_fund_gap_and_surplus(self) -> None:
        gap = evaluate_emergency_fund(balance=25000, monthly_budget=10000, required_months=3)
        surplus = evaluate_emergency_fund(balance=35000, monthly_budget=10000, required_months=3)

        self.assertEqual(gap.gap, 5000)
        self.assertEqual(gap.status, "below_target")
        self.assertEqual(surplus.surplus, 5000)
        self.assertEqual(surplus.status, "at_or_above_target")

    def test_calculates_available_surplus_after_reserves(self) -> None:
        self.assertEqual(calculate_available_surplus(42000, 30000, 2500), 9500)

    def test_annual_future_expense_reserve_uses_inclusive_calendar_months(self) -> None:
        reserve = calculate_future_expense_reserve(
            {"amount": 1800, "month": "2026-04-01"},
            today=date(2026, 1, 15),
        )

        self.assertEqual(reserve.months_remaining, 4)
        self.assertEqual(reserve.monthly_reserve, 450)

    def test_desire_affordable_now(self) -> None:
        result = evaluate_desire_affordability(
            {"name": "Guitar", "estimated_cost": 1000, "necessity": "Useful"},
            {
                "latest_balance": 45000,
                "budget": {"total": 10000},
                "expenses": {"total": 2000},
                "future_expenses": [],
                "today": "2026-07-04",
            },
        )

        self.assertEqual(result.level, "affordable_now")
        self.assertEqual(result.available_after_emergency, 15000)

    def test_desire_not_recommended_when_it_breaks_emergency_fund(self) -> None:
        result = evaluate_desire_affordability(
            {"name": "MacBook", "estimated_cost": 9000},
            {
                "latest_balance": 33500,
                "budget": {"total": 10000},
                "expenses": {"total": 1500},
                "future_expenses": [],
                "today": "2026-07-04",
            },
        )

        self.assertEqual(result.level, "not_recommended")
        self.assertIn("emergency fund", result.reasons[0])

    def test_desire_affordable_with_plan_when_month_budget_is_tight(self) -> None:
        result = evaluate_desire_affordability(
            {"name": "Trip", "estimated_cost": 2500},
            {
                "latest_balance": 50000,
                "budget": {"total": 10000},
                "expenses": {"total": 9500},
                "future_expenses": [],
                "today": "2026-07-04",
            },
        )

        self.assertEqual(result.level, "affordable_with_plan")
        self.assertGreaterEqual(result.saving_months, 1)

    def test_missing_data_returns_no_recommendation(self) -> None:
        result = evaluate_desire_affordability(
            {"name": "Guitar"},
            {"budget": {"total": 10000}, "today": "2026-07-04"},
        )

        self.assertEqual(result.level, "needs_more_info")
        self.assertIn("estimated_cost", result.missing)
        self.assertIn("latest_balance", result.missing)

    def test_scores_desire_deterministically(self) -> None:
        result = evaluate_desire_affordability(
            {"estimated_cost": 1000},
            {
                "latest_balance": 45000,
                "budget": {"total": 10000},
                "expenses": {"total": 2000},
                "future_expenses": [],
                "today": "2026-07-04",
            },
        )

        score = score_desire(
            {
                "desire_strength": 8,
                "necessity": "Useful",
                "time_horizon": "1-3 Months",
            },
            result,
        )

        self.assertEqual(score, 26)


if __name__ == "__main__":
    unittest.main()
