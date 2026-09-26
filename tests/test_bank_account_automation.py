"""Tests for the bank importer's trusted automation adapter."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from bank_account.automation import build_bank_account_automation_tools
from bank_account.config import BankAccountSettings, load_bank_account_settings


def _settings(tmp_path: Path) -> BankAccountSettings:
    return BankAccountSettings(
        inbox_path=tmp_path / "inbox",
        ledger_path=tmp_path / "imports.sqlite3",
        notion_token="",
        notion_database_id="database-id",
        notion_data_source_id=None,
        scan_interval_seconds=86400,
    )


def test_new_bank_record_uses_agent_token_and_accepts_empty_arguments(
    tmp_path: Path,
) -> None:
    calls: list[tuple[BankAccountSettings, str]] = []

    async def runner(
        settings: BankAccountSettings, notion_token: str
    ) -> dict[str, Any]:
        calls.append((settings, notion_token))
        return {"message": "imported"}

    settings = _settings(tmp_path)
    (automation,) = build_bank_account_automation_tools(
        settings, "shared-agent-token", runner=runner
    )

    result = asyncio.run(automation.ainvoke({}))

    assert automation.name == "new_bank_record"
    assert calls == [(settings, "shared-agent-token")]
    assert result == {"message": "imported"}


def test_bank_settings_use_finance_token_and_host_inbox_for_local_runs(
    monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    inbox = tmp_path / "bank exports"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FINANCE_AGENT_NOTION_TOKEN", "shared-token")
    monkeypatch.setenv("BANK_ACCOUNT_NOTION_DATABASE_ID", "database-id")
    monkeypatch.setenv("BANK_ACCOUNT_NOTION_TOKEN", "ignored-old-token")
    monkeypatch.delenv("BANK_ACCOUNT_INBOX", raising=False)
    monkeypatch.setenv("BANK_ACCOUNT_INBOX_HOST", str(inbox))

    settings = load_bank_account_settings()

    assert settings.notion_token == "shared-token"
    assert settings.inbox_path == inbox


def test_bank_settings_reject_shell_escaped_inbox_path(
    monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BANK_ACCOUNT_INBOX", raising=False)
    monkeypatch.setenv("BANK_ACCOUNT_INBOX_HOST", "/tmp/Bank\\ Record")

    with pytest.raises(ValueError, match="without shell escaping"):
        load_bank_account_settings(require_notion=False, prefer_host_inbox=True)
