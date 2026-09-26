"""File-lifecycle and idempotency tests for bank imports."""

import asyncio
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook

from bank_account.excel import read_bank_export
from bank_account.ledger import ImportLedger
from bank_account.models import BankTransaction
from bank_account.notion import ExistingTransaction, ExistingTransactions
from bank_account.workflow import BankImportWorkflow, excel_files


class FakeSink:
    def __init__(
        self,
        *,
        fail_after: int | None = None,
        existing: ExistingTransactions | None = None,
    ) -> None:
        self.created: list[BankTransaction] = []
        self.fail_after = fail_after
        self.existing = existing or ExistingTransactions(by_uid={}, by_visible_key={})
        self.validated = 0

    async def validate_schema(self) -> None:
        self.validated += 1

    async def existing_transactions(
        self, transactions: Sequence[BankTransaction]
    ) -> ExistingTransactions:
        return self.existing

    async def create(self, transaction: BankTransaction) -> str:
        if self.fail_after is not None and len(self.created) >= self.fail_after:
            raise RuntimeError("simulated Notion failure")
        self.created.append(transaction)
        return f"page-{len(self.created)}"


def test_missing_inbox_is_reported_instead_of_silently_created(tmp_path: Path) -> None:
    inbox = tmp_path / "mistyped inbox"

    with pytest.raises(FileNotFoundError, match="does not exist"):
        excel_files(inbox)

    assert not inbox.exists()


def _export(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    for _ in range(4):
        sheet.append(["metadata"])
    sheet.append(
        [
            "תאריך",
            "הפעולה",
            "פרטים",
            "אסמכתא",
            "חובה",
            "זכות",
            "יתרה בשח",
            "תאריך ערך",
            "לטובת",
            "עבור",
        ]
    )
    sheet.append([date(2026, 9, 1), "א", "one", "1", 10, None, 100, "", "", ""])
    sheet.append([date(2026, 9, 2), "ב", "two", "2", None, 20, 120, "", "", ""])
    workbook.save(path)


def test_dry_run_never_writes_or_deletes(tmp_path: Path) -> None:
    path = tmp_path / "bank.xlsx"
    _export(path)
    sink = FakeSink()

    result = asyncio.run(BankImportWorkflow(sink).import_file(path, dry_run=True))

    assert result.transactions == 2
    assert result.dry_run is True
    assert path.exists()
    assert sink.created == []
    assert sink.validated == 0


def test_success_records_rows_and_deletes_file(tmp_path: Path) -> None:
    path = tmp_path / "bank.xlsx"
    _export(path)
    sink = FakeSink()
    ledger = ImportLedger(tmp_path / "ledger.sqlite3")

    result = asyncio.run(BankImportWorkflow(sink, ledger).import_file(path))

    assert result.created == 2
    assert result.deleted is True
    assert not path.exists()
    assert all(ledger.has_transaction(item.fingerprint) for item in sink.created)


def test_failure_keeps_file_and_retry_skips_completed_rows(tmp_path: Path) -> None:
    path = tmp_path / "bank.xlsx"
    _export(path)
    ledger = ImportLedger(tmp_path / "ledger.sqlite3")
    failing = FakeSink(fail_after=1)

    with pytest.raises(RuntimeError, match="simulated Notion failure"):
        asyncio.run(BankImportWorkflow(failing, ledger).import_file(path))

    assert path.exists()
    succeeding = FakeSink()
    result = asyncio.run(BankImportWorkflow(succeeding, ledger).import_file(path))
    assert result.created == 1
    assert result.skipped == 1
    assert not path.exists()


def test_conflicting_uid_does_not_block_remaining_transactions(tmp_path: Path) -> None:
    path = tmp_path / "bank.xlsx"
    _export(path)
    parsed = read_bank_export(path)
    key = ("different",) * len(parsed[0].visible_key())
    sink = FakeSink(
        existing=ExistingTransactions(
            by_uid={parsed[0].uid: ExistingTransaction("existing-page", key)},
            by_visible_key={},
        )
    )

    result = asyncio.run(
        BankImportWorkflow(sink, ImportLedger(tmp_path / "ledger.sqlite3")).import_file(path)
    )

    assert result.conflicts == 1
    assert result.created == 1
    assert sink.created == [parsed[1]]
    assert path.exists()


def test_overlapping_export_skips_rows_with_existing_uids(tmp_path: Path) -> None:
    path = tmp_path / "overlap.xlsx"
    _export(path)
    parsed = read_bank_export(path)
    existing = {
        transaction.uid: ExistingTransaction(f"existing-{index}", transaction.visible_key())
        for index, transaction in enumerate(parsed, start=1)
    }
    sink = FakeSink(existing=ExistingTransactions(by_uid=existing, by_visible_key={}))

    result = asyncio.run(
        BankImportWorkflow(sink, ImportLedger(tmp_path / "ledger.sqlite3")).import_file(path)
    )

    assert result.created == 0
    assert result.skipped == 2
    assert sink.created == []
    assert not path.exists()
