"""Durable recurring-task and deferred-notice behavior."""

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from financial_agent.services.finance_queries import FinanceQueryService
from financial_agent.tools.registry import build_tool_catalog

from bruno.scheduling import SQLiteAssistantStore, cron_matches
from tests.fakes import FakeFinanceReader


def test_numeric_five_field_cron_uses_requested_timezone_calendar() -> None:
    monday = datetime(2026, 9, 28, 8, 30, tzinfo=ZoneInfo("Asia/Jerusalem"))

    assert cron_matches("30 8 * * 1-5", monday)
    assert not cron_matches("30 9 * * 1-5", monday)


def test_recurring_tasks_are_scoped_to_the_conversation(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteAssistantStore(tmp_path / "scheduler.sqlite3")
        await store.create(
            owner_thread_id="telegram:123:finance",
            prompt="Review this month's budget.",
            cron="0 9 * * 1",
            timezone="Asia/Jerusalem",
        )
        catalog = build_tool_catalog(
            FinanceQueryService(FakeFinanceReader()), recurring_tasks=store
        )
        read_tool = next(
            tool for tool in catalog.read if tool.name == "get_recurring_tasks"
        )

        result = await read_tool.ainvoke(
            {}, config={"configurable": {"thread_id": "telegram:123:finance"}}
        )

        assert len(result) == 1
        assert result[0]["prompt"] == "Review this month's budget."
        assert any(tool.name == "create_recurring_task" for tool in catalog.write)

    asyncio.run(scenario())


def test_deferred_notices_are_delivered_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = SQLiteAssistantStore(tmp_path / "scheduler.sqlite3")
        await store.defer_notice("123", "Watch Groceries next time.")

        assert await store.pop_notices("123") == ("Watch Groceries next time.",)
        assert await store.pop_notices("123") == ()

    asyncio.run(scenario())
