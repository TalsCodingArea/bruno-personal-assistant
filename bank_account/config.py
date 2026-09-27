"""Environment-backed settings for the standalone bank importer."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


@dataclass(frozen=True, slots=True)
class BankAccountSettings:
    """Configuration kept separate from the conversational finance agent."""

    inbox_path: Path
    ledger_path: Path
    notion_token: str
    notion_database_id: str
    notion_data_source_id: str | None
    scan_interval_seconds: float


def load_bank_account_settings(
    *, require_notion: bool = True, prefer_host_inbox: bool = False
) -> BankAccountSettings:
    file_values = dotenv_values(".env")

    def value(name: str, default: str = "") -> str:
        configured = os.getenv(name)
        if configured is None:
            configured = file_values.get(name)
        return configured if isinstance(configured, str) else default

    token = value("FINANCE_AGENT_NOTION_TOKEN").strip()
    database_id = value("BANK_ACCOUNT_NOTION_DATABASE_ID").strip()
    if require_notion:
        missing = []
        if not token:
            missing.append("FINANCE_AGENT_NOTION_TOKEN")
        if not database_id:
            missing.append("BANK_ACCOUNT_NOTION_DATABASE_ID")
        if missing:
            raise ValueError(f"Missing bank importer settings: {', '.join(missing)}")
    interval = float(value("BANK_ACCOUNT_SCAN_INTERVAL_SECONDS", "86400"))
    if interval < 60:
        raise ValueError("BANK_ACCOUNT_SCAN_INTERVAL_SECONDS must be at least 60")
    data_source_id = value("BANK_ACCOUNT_NOTION_DATA_SOURCE_ID").strip() or None
    runtime_inbox = value("BANK_ACCOUNT_INBOX").strip()
    host_inbox = value("BANK_ACCOUNT_INBOX_HOST").strip()
    inbox = (
        (host_inbox or runtime_inbox) if prefer_host_inbox else (runtime_inbox or host_inbox)
    ) or "bank-inbox"
    if "\\" in inbox:
        raise ValueError(
            "Bank inbox paths in .env must be written without shell escaping; "
            "remove backslashes before spaces and '~' characters"
        )
    return BankAccountSettings(
        inbox_path=Path(inbox),
        ledger_path=Path(value("BANK_ACCOUNT_LEDGER_PATH", ".bank-account/imports.sqlite3")),
        notion_token=token,
        notion_database_id=database_id,
        notion_data_source_id=data_source_id,
        scan_interval_seconds=interval,
    )
