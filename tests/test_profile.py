"""Tests for structured Notion profile reads and versioned writes."""

import asyncio
from datetime import UTC, datetime

import pytest
from financial_agent.domain.profile import FinancialProfileUpdateDraft, ProfileKind
from financial_agent.integrations.notion_profile import NotionFinancialProfileRepository
from financial_agent.services.profile import FinancialProfileService, ProfileVersionConflict

from tests.fakes import FakeNotion


def profile_page(
    page_id: str,
    *,
    key: str,
    statement: str,
    status: str = "Active",
    last_edited: str = "2026-08-22T09:00:00.000Z",
) -> dict[str, object]:
    return {
        "id": page_id,
        "created_time": "2026-08-20T09:00:00.000Z",
        "last_edited_time": last_edited,
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Groceries split"}]},
            "Key": {"type": "rich_text", "rich_text": [{"plain_text": key}]},
            "Kind": {"type": "select", "select": {"name": "Preference"}},
            "Scope": {"type": "multi_select", "multi_select": [{"name": "Spending"}]},
            "Statement": {
                "type": "rich_text",
                "rich_text": [{"plain_text": statement}],
            },
            "Status": {"type": "status", "status": {"name": status}},
            "Supersedes": {"type": "relation", "relation": []},
            "Operation ID": {
                "type": "unique_id",
                "unique_id": {"prefix": "CTX", "number": 7},
            },
        },
    }


def test_profile_reader_queries_only_active_compact_properties() -> None:
    notion = FakeNotion(
        {"profile": [profile_page("page-1", key="spending.split", statement="50%") ]}
    )
    repository = NotionFinancialProfileRepository(notion, "profile")

    entries = asyncio.run(repository.active_entries())

    assert entries[0].operation_id == "CTX-7"
    assert entries[0].statement == "50%"
    assert notion.queries[0]["filter"] == {
        "and": [
            {"property": "Status", "status": {"equals": "Active"}},
            {
                "property": "Scope",
                "multi_select": {"does_not_contain": "Operational Context"},
            },
        ]
    }


def test_profile_service_creates_new_version_then_supersedes_previous() -> None:
    notion = FakeNotion(
        {"profile": [profile_page("page-1", key="spending.split", statement="50%")]}
    )
    service = FinancialProfileService(NotionFinancialProfileRepository(notion, "profile"))

    async def run() -> None:
        draft = await service.draft_update(
            name="Groceries split",
            key="spending.split",
            kind=ProfileKind.PREFERENCE,
            scopes=("Spending",),
            statement="Use 60% for Tal's budget.",
            rationale="Tal changed the split.",
        )
        await service.apply_update(draft)

    asyncio.run(run())

    assert notion.created[0]["properties"]["Supersedes"] == {
        "relation": [{"id": "page-1"}]
    }
    assert notion.updated == [
        {
            "page_id": "page-1",
            "properties": {"Status": {"status": {"name": "Superseded"}}},
        }
    ]


def test_profile_service_rejects_a_stale_draft() -> None:
    notion = FakeNotion(
        {"profile": [profile_page("new-page", key="spending.split", statement="60%")]}
    )
    service = FinancialProfileService(NotionFinancialProfileRepository(notion, "profile"))
    stale = FinancialProfileUpdateDraft(
        name="Groceries split",
        key="spending.split",
        kind=ProfileKind.PREFERENCE,
        scopes=("Spending",),
        statement="70%",
        rationale="Changed",
        current_page_id="old-page",
        current_statement="50%",
        current_last_edited_at=datetime(2026, 8, 20, tzinfo=UTC),
    )

    try:
        asyncio.run(service.apply_update(stale))
    except ProfileVersionConflict as exc:
        assert "changed after this draft" in str(exc)
    else:
        raise AssertionError("Expected stale profile draft to be rejected")


def test_profile_service_rejects_system_operational_key_namespace() -> None:
    service = FinancialProfileService(
        NotionFinancialProfileRepository(FakeNotion({"profile": []}), "profile")
    )

    with pytest.raises(ValueError, match="reserved"):
        asyncio.run(
            service.draft_update(
                name="Unsafe runtime row",
                key="monitoring.runtime.2026-08.income_missing.global",
                kind=ProfileKind.DECISION,
                scopes=("Operational Context",),
                statement="{}",
                rationale="Should be rejected from ordinary profile writes.",
            )
        )
