"""Trusted Telegram automation entry point for bank imports."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.tools import BaseTool, tool

from bank_account.config import BankAccountSettings
from bank_account.ledger import ImportLedger
from bank_account.notion import NotionBankRepository
from bank_account.workflow import BankImportWorkflow, excel_files

BankImportRunner = Callable[[BankAccountSettings, str], Awaitable[dict[str, Any]]]


async def run_bank_import(
    settings: BankAccountSettings, notion_token: str
) -> dict[str, Any]:
    """Scan the configured inbox and import every Excel export."""

    if not settings.notion_database_id:
        raise ValueError("Missing BANK_ACCOUNT_NOTION_DATABASE_ID")
    ledger = ImportLedger(settings.ledger_path)
    with ledger.exclusive_run():
        paths = excel_files(settings.inbox_path)
        if not paths:
            return {
                "status": "no_files",
                "files": 0,
                "transactions": 0,
                "created": 0,
                "skipped": 0,
                "message": f"No bank Excel files found in {settings.inbox_path}.",
            }

        transactions = 0
        created = 0
        skipped = 0
        conflicts = 0
        async with NotionBankRepository(
            notion_token,
            database_id=settings.notion_database_id,
            data_source_id=settings.notion_data_source_id,
        ) as sink:
            workflow = BankImportWorkflow(sink, ledger)
            for path in paths:
                result = await workflow.import_file(path)
                transactions += result.transactions
                created += result.created
                skipped += result.skipped
                conflicts += result.conflicts

    return {
        "status": "partial" if conflicts else "complete",
        "files": len(paths),
        "transactions": transactions,
        "created": created,
        "skipped": skipped,
        "conflicts": conflicts,
        "message": (
            (
                "⚠️ Bank import completed with conflicts: "
                if conflicts
                else "✅ Bank import complete: "
            )
            + f"{len(paths)} file(s), {transactions} transaction(s), "
            f"{created} created, {skipped} skipped, {conflicts} conflicts."
        ),
    }


def build_bank_account_automation_tools(
    settings: BankAccountSettings,
    notion_token: str,
    *,
    runner: BankImportRunner = run_bank_import,
) -> tuple[BaseTool, ...]:
    """Expose the bank importer through Bruno's trusted automation registry."""

    @tool("new_bank_record")
    async def new_bank_record() -> dict[str, Any]:
        """Import new bank-account Excel exports from the configured inbox."""

        return await runner(settings, notion_token)

    return (new_bank_record,)
