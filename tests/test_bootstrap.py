"""Tests for the concrete Notion-backed dependency chain."""

import asyncio

from financial_agent.bootstrap import build_finance_application
from financial_agent.config import Settings

SOURCE_SETTINGS = {
    "expenses_data_source_id": "expenses-test",
    "income_data_source_id": "income-test",
    "budgets_data_source_id": "budgets-test",
    "future_expenses_data_source_id": "future-test",
    "financial_rules_data_source_id": "profile-test",
}


def test_bootstrap_connects_safe_tools_to_real_notion_sdk_without_requesting_data() -> None:
    settings = Settings(_env_file=None, notion_token="test-secret", **SOURCE_SETTINGS)

    application = build_finance_application(settings)

    assert {tool.name for tool in application.tools.conversation} == {
        "get_expense_category_context",
        "get_current_reimbursement",
        "get_current_credit_debt",
        "get_monthly_summary",
        "get_uncategorized_transactions",
        "suggest_categories",
        "get_budget_status",
        "forecast_month_end",
        "get_upcoming_planned_expenses",
        "draft_category_updates",
        "draft_planned_expense",
        "get_financial_profile",
        "draft_financial_profile_update",
        "get_interaction_profile",
        "draft_interaction_preference_update",
            "get_expense_monitoring_decisions",
            "get_budget_planning_context",
            "draft_monthly_budget_plan",
    }
    assert {tool.name for tool in application.tools.write} == {
        "correct_expense_category",
        "apply_financial_profile_update",
        "apply_interaction_preference_update",
        "apply_monthly_budget_plan",
    }
    asyncio.run(application.aclose())


def test_bootstrap_requires_notion_token() -> None:
    settings = Settings(_env_file=None, notion_token=None)

    try:
        build_finance_application(settings)
    except ValueError as exc:
        assert "FINANCE_AGENT_NOTION_TOKEN" in str(exc)
    else:
        raise AssertionError("Expected missing Notion token to fail during bootstrap")
