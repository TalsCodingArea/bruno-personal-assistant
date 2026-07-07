from __future__ import annotations

import unittest

from personal_assistant.router import classify_intent


class NeverCalledLLM:
    async def ainvoke(self, messages):
        raise AssertionError("finance fast path should avoid the LLM")


class FinancialAdvisorRouterTest(unittest.IsolatedAsyncioTestCase):
    async def test_finance_fast_paths(self) -> None:
        cases = [
            "Can I afford a MacBook?",
            "I want to buy a guitar",
            "My bank balance is 42000",
            "Every April I pay car license",
            "Show my spending this week",
            "Show my future purchases",
            "Show my future vacations",
            "Help me save up for a camera",
        ]

        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(await classify_intent(NeverCalledLLM(), text), "finance")

    async def test_monthly_budget_routes_to_finance_capability(self) -> None:
        self.assertEqual(await classify_intent(NeverCalledLLM(), "Start monthly budget"), "finance")


if __name__ == "__main__":
    unittest.main()
