"""Tests for the intentionally read/draft-only first tool catalog."""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.integrations.expense_monitor_ledger import InMemoryExpenseMonitorLedger
from app.integrations.notion_profile import NotionFinancialProfileRepository
from app.services.budget_planning import BudgetPlanningService
from app.services.finance_queries import FinanceQueryService
from app.services.interaction import InteractionProfileService
from app.services.profile import FinancialProfileService
from app.tools import build_tool_catalog
from tests.fakes import FakeFinanceReader, FakeNotion
from tests.test_budget_planning import CreationRepository, Rules


def _json_schema_patterns(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "pattern" and isinstance(child, str):
                yield child
            else:
                yield from _json_schema_patterns(child)
    elif isinstance(value, list):
        for child in value:
            yield from _json_schema_patterns(child)


def test_catalog_contains_only_requested_read_and_draft_tools() -> None:
    catalog = build_tool_catalog(FinanceQueryService(FakeFinanceReader()))

    assert {tool.name for tool in catalog.read} == {
        "get_monthly_summary",
        "get_uncategorized_transactions",
        "suggest_categories",
        "get_budget_status",
        "forecast_month_end",
        "get_upcoming_planned_expenses",
    }
    assert {tool.name for tool in catalog.draft} == {
        "draft_category_updates",
        "draft_planned_expense",
    }
    assert not {
        "log_expense",
        "log_income",
        "set_monthly_budget",
        "create_financial_rule",
    }.intersection(tool.name for tool in catalog.conversation)


def test_monitoring_context_tool_is_read_only_and_opt_in() -> None:
    catalog = build_tool_catalog(
        FinanceQueryService(FakeFinanceReader()),
        monitoring_ledger=InMemoryExpenseMonitorLedger(),
    )

    tool = next(
        item for item in catalog.read if item.name == "get_expense_monitoring_decisions"
    )
    result = asyncio.run(tool.ainvoke({"month": "2026-08"}))

    assert result == []
    assert tool not in catalog.write


def test_budget_tools_support_context_then_agent_directed_draft() -> None:
    finance_reader = FakeFinanceReader()
    planning = BudgetPlanningService(
        finance_reader,
        Rules(),
        CreationRepository(),
    )
    catalog = build_tool_catalog(
        FinanceQueryService(finance_reader),
        budget_planning=planning,
    )
    context_tool = next(
        item for item in catalog.read if item.name == "get_budget_planning_context"
    )
    draft_tool = next(
        item for item in catalog.draft if item.name == "draft_monthly_budget_plan"
    )

    async def run() -> object:
        context = await context_tool.ainvoke({"month": "2026-09"})
        return await draft_tool.ainvoke(
            {
                "month": "2026-09",
                "source_fingerprint": context["source_fingerprint"],
                "financial_cap": "1500",
                "cap_basis": "user_provided",
                "cap_rationale": "Tal supplied the cap.",
                "items": [
                    {
                        "subcategory": "Rent",
                        "amount": "1000",
                        "progressive": "Accumulated",
                        "volatility_percent": "0",
                        "purpose": "regular",
                        "rationale": "Stable rent allocation.",
                    }
                ],
            }
        )

    draft = asyncio.run(run())

    assert draft["financial_cap"] == "1500.00"
    assert draft["total_budget_after"] == "1000.00"
    assert draft["variable_reserve_after"] is None
    assert draft["warnings"]


def test_planned_expense_draft_is_calculated_without_a_write() -> None:
    catalog = build_tool_catalog(FinanceQueryService(FakeFinanceReader()))
    draft_tool = next(tool for tool in catalog.draft if tool.name == "draft_planned_expense")

    result = asyncio.run(
        draft_tool.ainvoke(
            {
                "name": "Laptop",
                "target_amount": "1200",
                "due_date": "2026-12-10",
                "today": "2026-08-21",
            }
        )
    )

    assert result["monthly_allocation"] == "400.00"
    assert result["saving_starts_on"] == "2026-10-01"


def test_agent_tool_schemas_do_not_contain_unsupported_regex_lookaround() -> None:
    profile = FinancialProfileService(
        NotionFinancialProfileRepository(FakeNotion(), "profile")
    )
    interaction = InteractionProfileService(profile)
    finance_reader = FakeFinanceReader()
    planning = BudgetPlanningService(
        finance_reader,
        profile,
        CreationRepository(),
    )
    catalog = build_tool_catalog(
        FinanceQueryService(finance_reader),
        profile,
        interaction,
        budget_planning=planning,
    )

    for agent_tool in catalog.all:
        openai_tool = convert_to_openai_tool(agent_tool, strict=True)
        patterns = _json_schema_patterns(openai_tool)
        assert all("(?" not in pattern for pattern in patterns), agent_tool.name


def test_forecast_converts_currency_strings_before_finance_calculation() -> None:
    catalog = build_tool_catalog(FinanceQueryService(FakeFinanceReader()))
    forecast_tool = next(tool for tool in catalog.read if tool.name == "forecast_month_end")

    result = asyncio.run(
        forecast_tool.ainvoke(
            {
                "month": "2026-08",
                "as_of": "2026-08-15",
                "expected_income": "5000.00",
                "planned_allocations": "300.00",
                "buffer": "2000.00",
            }
        )
    )

    assert result["expected_income"] == "5000.00"
    assert result["planned_allocations"] == "300.00"
    assert result["buffer"] == "2000.00"


def test_generic_profile_tools_cannot_bypass_interaction_validation() -> None:
    profile = FinancialProfileService(
        NotionFinancialProfileRepository(FakeNotion(), "profile")
    )
    interaction = InteractionProfileService(profile)
    catalog = build_tool_catalog(
        FinanceQueryService(FakeFinanceReader()), profile, interaction
    )
    generic_draft = next(
        tool for tool in catalog.draft if tool.name == "draft_financial_profile_update"
    )

    with pytest.raises(ValueError, match="draft_interaction_preference_update"):
        asyncio.run(
            generic_draft.ainvoke(
                {
                    "name": "Unvalidated banter",
                    "key": "assistant.banter",
                    "kind": "Preference",
                    "scopes": ["Conversation"],
                    "statement": "anything-goes",
                    "rationale": "Attempted namespace bypass.",
                }
            )
        )
