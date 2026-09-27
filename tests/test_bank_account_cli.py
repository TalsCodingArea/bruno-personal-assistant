"""Command-line regression tests for the bank importer."""

import os
import subprocess
import sys
from pathlib import Path

from bank_account import __main__ as bank_main
from bank_account.config import BankAccountSettings


def test_no_arguments_runs_live_flow_from_configured_host_inbox(tmp_path: Path) -> None:
    inbox = tmp_path / "bank exports"
    inbox.mkdir()
    environment = os.environ.copy()
    environment["FINANCE_AGENT_NOTION_TOKEN"] = "test-token"
    environment["BANK_ACCOUNT_NOTION_DATABASE_ID"] = "database-id"
    environment["BANK_ACCOUNT_INBOX_HOST"] = str(inbox)
    environment["BANK_ACCOUNT_INBOX"] = str(tmp_path / "container-only-path")

    result = subprocess.run(
        [sys.executable, "bank_account/__main__.py"],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "LIVE DEBUG" in result.stderr
    assert f"No bank Excel files found in {inbox}" in result.stderr


def test_no_argument_debug_uses_same_runner_as_telegram(
    monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    settings = BankAccountSettings(
        inbox_path=tmp_path,
        ledger_path=tmp_path / "ledger.sqlite3",
        notion_token="shared-token",
        notion_database_id="database-id",
        notion_data_source_id=None,
        scan_interval_seconds=86400,
    )
    calls: list[tuple[BankAccountSettings, str]] = []

    async def runner(
        configured: BankAccountSettings, notion_token: str
    ) -> dict[str, object]:
        calls.append((configured, notion_token))
        return {"message": "telegram path ran"}

    async def legacy_runner(*args: object, **kwargs: object) -> int:
        raise AssertionError("direct debug used the legacy CLI workflow")

    monkeypatch.setattr(sys, "argv", ["bank_account/__main__.py"])
    monkeypatch.setattr(bank_main, "load_bank_account_settings", lambda **_: settings)
    monkeypatch.setattr(bank_main, "run_bank_import", runner, raising=False)
    monkeypatch.setattr(bank_main, "_run_once", legacy_runner)

    bank_main.main()

    assert calls == [(settings, "shared-token")]
