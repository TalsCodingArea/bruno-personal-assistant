"""Persistent idempotency ledger for bank imports."""

import fcntl
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class ImportLedger:
    """Track rows and completed files so retries do not create duplicates."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS imported_transactions (
                    fingerprint TEXT PRIMARY KEY,
                    notion_page_id TEXT NOT NULL,
                    source_filename TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS imported_files (
                    file_hash TEXT PRIMARY KEY,
                    source_filename TEXT NOT NULL,
                    transaction_count INTEGER NOT NULL,
                    imported_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def exclusive_run(self) -> Iterator[None]:
        """Prevent the daily scan and Telegram trigger from importing together."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")
        with lock_path.open("a") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def has_transaction(self, fingerprint: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM imported_transactions WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return row is not None

    def record_transaction(
        self, fingerprint: str, notion_page_id: str, source_filename: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO imported_transactions
                    (fingerprint, notion_page_id, source_filename, imported_at)
                VALUES (?, ?, ?, ?)
                """,
                (fingerprint, notion_page_id, source_filename, _now()),
            )

    def has_file(self, file_hash: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM imported_files WHERE file_hash = ?", (file_hash,)
            ).fetchone()
        return row is not None

    def record_file(self, file_hash: str, source_filename: str, transaction_count: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO imported_files
                    (file_hash, source_filename, transaction_count, imported_at)
                VALUES (?, ?, ?, ?)
                """,
                (file_hash, source_filename, transaction_count, _now()),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


def _now() -> str:
    return datetime.now(UTC).isoformat()
