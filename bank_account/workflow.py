"""Safe file lifecycle for importing bank movements."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from bank_account.excel import read_bank_export
from bank_account.ledger import ImportLedger
from bank_account.notion import BankTransactionSink, ExistingTransaction


@dataclass(frozen=True, slots=True)
class ImportResult:
    file: Path
    transactions: int
    created: int
    skipped: int
    deleted: bool
    dry_run: bool
    conflicts: int = 0


class BankImportWorkflow:
    """Parse first, persist idempotently, then delete only after full success."""

    def __init__(
        self, sink: BankTransactionSink | None, ledger: ImportLedger | None = None
    ) -> None:
        self.sink = sink
        self.ledger = ledger

    async def import_file(self, path: Path, *, dry_run: bool = False) -> ImportResult:
        transactions = read_bank_export(path)
        if dry_run:
            return ImportResult(path, len(transactions), 0, 0, False, True)
        if self.sink is None or self.ledger is None:
            raise RuntimeError("A Notion sink and import ledger are required outside dry-run mode")

        self.ledger.initialize()
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if self.ledger.has_file(file_hash):
            path.unlink()
            return ImportResult(path, len(transactions), 0, len(transactions), True, False)

        await self.sink.validate_schema()
        remote = await self.sink.existing_transactions(transactions)
        by_uid = dict(remote.by_uid)
        by_visible_key = dict(remote.by_visible_key)
        created = 0
        skipped = 0
        conflicts = 0
        for transaction in transactions:
            if self.ledger.has_transaction(transaction.fingerprint):
                skipped += 1
                continue
            existing = by_uid.get(transaction.uid)
            if existing is not None:
                if existing.visible_key != transaction.visible_key():
                    conflicts += 1
                    continue
                self.ledger.record_transaction(transaction.fingerprint, existing.page_id, path.name)
                skipped += 1
                continue
            existing_page_id = by_visible_key.get(transaction.visible_key())
            if existing_page_id is not None:
                self.ledger.record_transaction(transaction.fingerprint, existing_page_id, path.name)
                skipped += 1
                continue
            page_id = await self.sink.create(transaction)
            self.ledger.record_transaction(transaction.fingerprint, page_id, path.name)
            by_uid[transaction.uid] = ExistingTransaction(page_id, transaction.visible_key())
            by_visible_key[transaction.visible_key()] = page_id
            created += 1

        if conflicts:
            return ImportResult(
                path,
                len(transactions),
                created,
                skipped,
                False,
                False,
                conflicts=conflicts,
            )
        self.ledger.record_file(file_hash, path.name, len(transactions))
        path.unlink()
        return ImportResult(path, len(transactions), created, skipped, True, False)


def excel_files(inbox: Path) -> tuple[Path, ...]:
    """Return stable-file candidates in deterministic order."""

    if not inbox.exists():
        raise FileNotFoundError(f"Bank inbox directory does not exist: {inbox}")
    if not inbox.is_dir():
        raise NotADirectoryError(f"Bank inbox path is not a directory: {inbox}")
    return tuple(
        sorted(
            (
                path
                for path in inbox.iterdir()
                if path.is_file() and path.suffix.casefold() in {".xlsx", ".xlsm"}
            ),
            key=lambda path: path.name.casefold(),
        )
    )
