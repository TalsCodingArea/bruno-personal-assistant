"""Command line and daily runner for bank-account imports."""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from bank_account.automation import run_bank_import
from bank_account.config import BankAccountSettings, load_bank_account_settings
from bank_account.excel import read_bank_export
from bank_account.ledger import ImportLedger
from bank_account.notion import NotionBankRepository
from bank_account.workflow import BankImportWorkflow, ImportResult, excel_files

LOGGER = logging.getLogger("bank_account")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import bank movements into Notion")
    subparsers = parser.add_subparsers(dest="command")
    debug = subparsers.add_parser("debug", help="parse one file without writing or deleting")
    debug.add_argument("file", type=Path)
    once = subparsers.add_parser("run-once", help="scan the inbox once")
    once.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("daemon", help="scan now and then once per configured interval")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    if args.command == "debug":
        _debug(args.file)
        return
    if args.command is None:
        settings = load_bank_account_settings(
            require_notion=True,
            prefer_host_inbox=True,
        )
        LOGGER.warning(
            "LIVE DEBUG: invoking new_bank_record for %s; successful files will be deleted",
            settings.inbox_path,
        )
        result = asyncio.run(run_bank_import(settings, settings.notion_token))
        LOGGER.info("%s", result["message"])
        return
    dry_run = getattr(args, "dry_run", False)
    settings = load_bank_account_settings(
        require_notion=not dry_run,
        prefer_host_inbox=False,
    )
    if args.command == "run-once":
        failed = asyncio.run(_run_once(settings, dry_run=dry_run))
        raise SystemExit(1 if failed else 0)
    asyncio.run(_daemon(settings))


def _debug(path: Path) -> None:
    transactions = read_bank_export(path)
    rows = [
        {
            "uid": item.uid,
            "title": item.title,
            "date": item.occurred_on.isoformat(),
            "select": item.direction.value,
            "amount": str(item.amount),
            "balance_after": str(item.balance_after),
            "description": item.description,
            "action": item.action,
            "fingerprint": item.fingerprint,
        }
        for item in transactions
    ]
    print(json.dumps(rows, ensure_ascii=False, indent=2))


async def _run_once(settings: BankAccountSettings, *, dry_run: bool) -> int:
    if dry_run:
        paths = excel_files(settings.inbox_path)
        if not paths:
            LOGGER.info("No Excel files found in %s", settings.inbox_path)
            return 0
        workflow = BankImportWorkflow(None)
        return await _process_paths(workflow, paths, dry_run=True)
    ledger = ImportLedger(settings.ledger_path)
    with ledger.exclusive_run():
        paths = excel_files(settings.inbox_path)
        if not paths:
            LOGGER.info("No Excel files found in %s", settings.inbox_path)
            return 0
        async with NotionBankRepository(
            settings.notion_token,
            database_id=settings.notion_database_id,
            data_source_id=settings.notion_data_source_id,
        ) as sink:
            workflow = BankImportWorkflow(sink, ledger)
            return await _process_paths(workflow, paths, dry_run=False)


async def _process_paths(
    workflow: BankImportWorkflow, paths: tuple[Path, ...], *, dry_run: bool
) -> int:
    failures = 0
    for path in paths:
        try:
            result = await workflow.import_file(path, dry_run=dry_run)
        except Exception:
            failures += 1
            LOGGER.exception("Import failed; file kept at %s", path)
        else:
            _log_result(result)
    return failures


def _log_result(result: ImportResult) -> None:
    LOGGER.info(
        "%s: transactions=%d created=%d skipped=%d conflicts=%d deleted=%s dry_run=%s",
        result.file.name,
        result.transactions,
        result.created,
        result.skipped,
        result.conflicts,
        result.deleted,
        result.dry_run,
    )


async def _daemon(settings: BankAccountSettings) -> None:
    while True:
        await _run_once(settings, dry_run=False)
        await asyncio.sleep(settings.scan_interval_seconds)


if __name__ == "__main__":
    main()
