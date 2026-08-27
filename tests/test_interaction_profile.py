"""Tests for structured interaction preferences and their Notion-backed tools."""

import asyncio
from datetime import UTC, datetime

from app.domain.interaction import BanterLevel, InteractionSetting, Verbosity
from app.domain.profile import FinancialProfileEntry, ProfileKind, ProfileStatus
from app.integrations.notion_profile import NotionFinancialProfileRepository
from app.services.finance_queries import FinanceQueryService
from app.services.interaction import (
    InteractionProfileService,
    compile_interaction_profile,
)
from app.services.profile import FinancialProfileService
from app.tools import build_tool_catalog
from tests.fakes import FakeFinanceReader, FakeNotion


def interaction_page(
    page_id: str,
    *,
    setting: InteractionSetting,
    value: str,
) -> dict[str, object]:
    return {
        "id": page_id,
        "created_time": "2026-08-20T09:00:00.000Z",
        "last_edited_time": "2026-08-22T09:00:00.000Z",
        "properties": {
            "Name": {
                "type": "title",
                "title": [{"plain_text": f"Assistant {setting.display_name}"}],
            },
            "Key": {
                "type": "rich_text",
                "rich_text": [{"plain_text": setting.key}],
            },
            "Kind": {"type": "select", "select": {"name": "Preference"}},
            "Scope": {
                "type": "multi_select",
                "multi_select": [{"name": "Conversation"}],
            },
            "Statement": {
                "type": "rich_text",
                "rich_text": [{"plain_text": value}],
            },
            "Status": {"type": "status", "status": {"name": "Active"}},
            "Supersedes": {"type": "relation", "relation": []},
            "Operation ID": {
                "type": "unique_id",
                "unique_id": {"prefix": "CTX", "number": 9},
            },
        },
    }


def test_compiler_overlays_valid_entries_and_reports_invalid_manual_edits() -> None:
    now = datetime(2026, 8, 22, tzinfo=UTC)
    entries = [
        FinancialProfileEntry(
            page_id="banter",
            name="Assistant Banter",
            key="assistant.banter",
            kind=ProfileKind.PREFERENCE,
            scopes=("Conversation",),
            statement="playful",
            status=ProfileStatus.ACTIVE,
            last_edited_at=now,
        ),
        FinancialProfileEntry(
            page_id="verbosity",
            name="Assistant Verbosity",
            key="assistant.verbosity",
            kind=ProfileKind.PREFERENCE,
            scopes=("Conversation",),
            statement="extremely_wordy",
            status=ProfileStatus.ACTIVE,
            last_edited_at=now,
        ),
    ]

    result = compile_interaction_profile(entries)

    assert result.banter is BanterLevel.PLAYFUL
    assert result.verbosity is Verbosity.CONCISE
    assert result.warnings and "assistant.verbosity" in result.warnings[0]


def test_interaction_tools_read_defaults_and_draft_a_versioned_update() -> None:
    notion = FakeNotion(
        {
            "profile": [
                interaction_page(
                    "banter-current",
                    setting=InteractionSetting.BANTER,
                    value="light",
                )
            ]
        }
    )
    profile = FinancialProfileService(NotionFinancialProfileRepository(notion, "profile"))
    interaction = InteractionProfileService(profile)
    catalog = build_tool_catalog(
        FinanceQueryService(FakeFinanceReader()), profile, interaction
    )
    read_tool = next(
        tool for tool in catalog.preference_read if tool.name == "get_interaction_profile"
    )
    draft_tool = next(
        tool
        for tool in catalog.draft
        if tool.name == "draft_interaction_preference_update"
    )

    async def run() -> tuple[object, object]:
        current = await read_tool.ainvoke({})
        draft = await draft_tool.ainvoke(
            {
                "setting": "banter",
                "value": "playful",
                "rationale": "Tal asked for more banter.",
            }
        )
        return current, draft

    current, draft = asyncio.run(run())

    assert current["banter"] == "light"
    assert draft["key"] == "assistant.banter"
    assert draft["statement"] == "playful"
    assert draft["current_page_id"] == "banter-current"
    assert notion.created == []
