from __future__ import annotations

import unittest
from typing import Any

from langchain_core.messages import AIMessage

from personal_assistant.agent.capabilities.financial_advisor.graph import (
    FinancialAdvisorRuntime,
    create_financial_advisor_graph,
)


class FakeRouterModel:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = responses
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any], config: dict[str, Any] | None = None) -> AIMessage:
        self.calls.append(messages)
        return self.responses.pop(0)


class FinancialAdvisorGraphTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.write_plans: list[dict[str, Any]] = []
        self.provider_calls: list[dict[str, Any]] = []

        def provider(sub_intent: str, period: dict[str, str], extracted: dict[str, Any]) -> dict[str, Any]:
            self.provider_calls.append(
                {
                    "sub_intent": sub_intent,
                    "period": period,
                    "extracted": extracted,
                    "context_needs": extracted.get("context_needs"),
                }
            )
            return {
                "today": "2026-07-04",
                "budget": {"total": 10000},
                "expenses": {"total": 2000, "by_category": {"Food": 1200}},
                "income": {"total": 12000},
                "balances": [{"account": "Main Checking", "balance": 33500}],
                "future_expenses": [],
                "future_purchases": [{"name": "Camera", "budget": 5000}],
                "future_vacations": [{"country": "Japan", "budget": 18000}],
                "advisor_rules": {"rules": []},
                "advisor_profile": {"emergency_fund_months": 3},
            }

        def writer(plan: dict[str, Any]) -> dict[str, Any]:
            self.write_plans.append(plan)
            return {"ok": True, "page_id": f"page-{len(self.write_plans)}"}

        self._provider = provider
        self._writer = writer
        self.runtime = FinancialAdvisorRuntime(
            create_financial_advisor_graph(
                None,
                data_provider=self._provider,
                write_executor=self._writer,
            )
        )

    async def test_desire_message_creates_write_plan_when_not_recommended(self) -> None:
        result = await self.runtime.ainvoke(
            {"input": "I want to buy a new MacBook around 9000 ILS"},
            config={"configurable": {"today": "2026-07-04"}},
        )

        state = result["state"]
        self.assertEqual(state["sub_intent"], "desire_affordability")
        self.assertEqual(state["evaluation"]["affordability"]["level"], "not_recommended")
        self.assertEqual(state["write_plan"]["action"], "create_future_purchase")
        self.assertEqual(self.write_plans[0]["payload"]["budget"], 9000)

    async def test_balance_update_creates_snapshot_plan(self) -> None:
        result = await self.runtime.ainvoke(
            {"input": "My bank balance is 42300 ILS"},
            config={"configurable": {"today": "2026-07-04"}},
        )

        state = result["state"]
        self.assertEqual(state["sub_intent"], "balance_update")
        self.assertEqual(state["write_plan"]["action"], "update_bank_account_balance")
        self.assertEqual(self.write_plans[0]["payload"]["balance"], 42300)

    async def test_future_expense_creates_future_expense_plan(self) -> None:
        result = await self.runtime.ainvoke(
            {"input": "Every April I need to pay 1800 ILS for car license"},
            config={"configurable": {"today": "2026-01-15"}},
        )

        state = result["state"]
        self.assertEqual(state["sub_intent"], "future_expense_capture")
        self.assertEqual(state["evaluation"]["reserve"]["monthly_reserve"], 450)
        self.assertEqual(state["write_plan"]["action"], "create_future_expense_with_savings")
        payload = self.write_plans[0]["payload"]
        self.assertEqual(payload["future_expense"]["month"], "2026-04-01")
        # Default rule: 500/month, max 3 months -> 1800 due April from January
        # means 3 saving months (Jan-Mar) split evenly at 600.
        schedule = state["evaluation"]["saving_schedule"]
        self.assertEqual(
            schedule["installments"],
            [
                {"month": "2026-01", "amount": 600.0},
                {"month": "2026-02", "amount": 600.0},
                {"month": "2026-03", "amount": 600.0},
            ],
        )
        self.assertEqual(payload["saving_plan"]["due_month"], "2026-04")

    async def test_vacation_lookup_flags_missing_planned_vacation(self) -> None:
        def empty_vacations_provider(sub_intent, period, extracted):
            context = self._provider(sub_intent, period, extracted)
            context["future_vacations"] = []
            return context

        runtime = FinancialAdvisorRuntime(
            create_financial_advisor_graph(
                None, data_provider=empty_vacations_provider, write_executor=self._writer
            )
        )
        result = await runtime.ainvoke(
            {"input": "What future vacations do I have planned?"},
            config={"configurable": {"today": "2026-07-04"}},
        )

        evaluation = result["state"]["evaluation"]["future_vacations"]
        self.assertEqual(evaluation["count"], 0)
        self.assertEqual(evaluation["min_planned_vacations"], 1)
        self.assertTrue(evaluation["needs_planning"])

    async def test_expense_summary_does_not_write(self) -> None:
        result = await self.runtime.ainvoke(
            {"input": "How am I doing this month?"},
            config={"configurable": {"today": "2026-07-04"}},
        )

        state = result["state"]
        self.assertEqual(state["sub_intent"], "expense_summary")
        self.assertIsNone(state["write_plan"]["action"])
        self.assertEqual(self.write_plans, [])

    async def test_expense_projection_uses_actual_date_not_period_start(self) -> None:
        result = await self.runtime.ainvoke(
            {"input": "How am I doing this month?"},
            config={"configurable": {"today": "2026-07-04"}},
        )

        state = result["state"]
        self.assertEqual(state["sub_intent"], "expense_summary")
        projection = state["evaluation"]["projection"]
        # Regression: `today` was derived from period["start"] (day 1 of the
        # month), inflating the pace projection to spent * days_in_month
        # (2000 / 1 * 31 = 62000 instead of 2000 / 4 * 31 = 15500).
        self.assertEqual(projection["spent_so_far"], 2000)
        self.assertEqual(projection["projected_month_total"], 15500.0)
        self.assertEqual(state["loaded_context"]["today"], "2026-07-04")

    async def test_general_finance_question_answers_without_forced_evaluation(self) -> None:
        result = await self.runtime.ainvoke(
            {"input": "What do you think about my financial situation?"},
            config={"configurable": {"today": "2026-07-04"}},
        )

        state = result["state"]
        self.assertEqual(state["sub_intent"], "general_finance_question")
        self.assertEqual(state["route"], "contextual_answer")
        self.assertNotIn("evaluation", state)
        self.assertEqual(self.write_plans, [])

    async def test_fallback_router_handles_ambiguous_future_payment(self) -> None:
        result = await self.runtime.ainvoke(
            {"input": "I have a payment in October"},
            config={"configurable": {"today": "2026-07-04"}},
        )

        state = result["state"]
        self.assertEqual(state["sub_intent"], "future_expense_review")
        self.assertEqual(state["route"], "deterministic_evaluation")
        self.assertIn("amount", state["evaluation"]["missing"])

    async def test_llm_router_declares_context_needs_and_extracted_desire(self) -> None:
        model = FakeRouterModel(
            [
                AIMessage(
                    content=(
                        '{"sub_intent":"desire_affordability",'
                        '"route":"deterministic_evaluation",'
                        '"context_needs":["rules","budget","expenses","income","balance","future_purchases"],'
                        '"extracted":{"desire":{"name":"expensive thing","estimated_cost":6000,'
                        '"category":"Other","desire_strength":6,"necessity":"Nice to Have",'
                        '"time_horizon":"Someday","reason":"I am considering buying something expensive"}}}'
                    )
                ),
                AIMessage(content="advisor response"),
            ]
        )
        runtime = FinancialAdvisorRuntime(
            create_financial_advisor_graph(
                model,
                data_provider=self._provider,
                write_executor=self._writer,
            )
        )

        result = await runtime.ainvoke(
            {"input": "I'm considering buying something expensive"},
            config={"configurable": {"today": "2026-07-04"}},
        )

        state = result["state"]
        self.assertEqual(state["sub_intent"], "desire_affordability")
        self.assertEqual(state["context_needs"], ["rules", "budget", "expenses", "income", "balance", "future_purchases"])
        self.assertEqual(state["extracted"]["desire"]["estimated_cost"], 6000)
        self.assertEqual(result["output"], "advisor response")

    async def test_llm_router_can_route_general_finance_question_to_contextual_answer(self) -> None:
        model = FakeRouterModel(
            [
                AIMessage(
                    content=(
                        '{"sub_intent":"general_finance_question",'
                        '"route":"contextual_answer",'
                        '"context_needs":["rules","budget","expenses","income","balance","future_purchases","future_vacations"],'
                        '"extracted":{}}'
                    )
                ),
                AIMessage(content="broad advisor response"),
            ]
        )
        runtime = FinancialAdvisorRuntime(
            create_financial_advisor_graph(
                model,
                data_provider=self._provider,
                write_executor=self._writer,
            )
        )

        result = await runtime.ainvoke(
            {"input": "Can I loosen up spending a bit?"},
            config={"configurable": {"today": "2026-07-04"}},
        )

        state = result["state"]
        self.assertEqual(state["sub_intent"], "general_finance_question")
        self.assertEqual(state["route"], "contextual_answer")
        self.assertNotIn("evaluation", state)
        self.assertEqual(result["output"], "broad advisor response")
        self.assertEqual(
            self.provider_calls[-1]["context_needs"],
            ["rules", "budget", "expenses", "income", "balance", "future_purchases", "future_vacations"],
        )

    async def test_future_purchase_lookup_lists_current_purchases(self) -> None:
        result = await self.runtime.ainvoke(
            {"input": "Show me my future purchases"},
            config={"configurable": {"today": "2026-07-04"}},
        )

        state = result["state"]
        self.assertEqual(state["sub_intent"], "future_purchase_lookup")
        self.assertEqual(state["evaluation"]["future_purchases"]["count"], 1)
        self.assertIn("Camera", result["output"])

    async def test_future_vacation_lookup_lists_current_vacations(self) -> None:
        result = await self.runtime.ainvoke(
            {"input": "Show me my future vacations"},
            config={"configurable": {"today": "2026-07-04"}},
        )

        state = result["state"]
        self.assertEqual(state["sub_intent"], "future_vacation_lookup")
        self.assertEqual(state["evaluation"]["future_vacations"]["count"], 1)
        self.assertIn("Japan", result["output"])

    async def test_saving_plan_prioritizes_three_month_bank_buffer(self) -> None:
        def provider(sub_intent: str, period: dict[str, str], extracted: dict[str, Any]) -> dict[str, Any]:
            return {
                "today": "2026-07-04",
                "budget": {"total": 7000},
                "expenses": {"total": 7000},
                "income": {"total": 12000},
                "balances": [{"account": "Main Checking", "balance": 10000}],
                "future_purchases": [{"name": "Camera", "budget": 5000}],
                "future_vacations": [],
                "advisor_profile": {"emergency_fund_months": 3},
                "advisor_rules": {"rules": []},
            }

        runtime = FinancialAdvisorRuntime(
            create_financial_advisor_graph(None, data_provider=provider, write_executor=self._writer)
        )
        result = await runtime.ainvoke(
            {"input": "Help me save up for a camera around 5000 ILS"},
            config={"configurable": {"today": "2026-07-04"}},
        )

        plan = result["state"]["evaluation"]["saving_plan"]
        self.assertEqual(plan["emergency_target"], 21000)
        self.assertEqual(plan["emergency_gap"], 11000)
        self.assertFalse(plan["can_start_item_saving"])
        self.assertIn("rebuild the bank buffer", result["output"])


if __name__ == "__main__":
    unittest.main()
