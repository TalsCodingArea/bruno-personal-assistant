"""Durable recurring tasks and deferred personal-chat notices."""

import asyncio
import sqlite3
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class RecurringTask:
    id: str
    owner_thread_id: str
    prompt: str
    cron: str
    timezone: str
    created_at: str
    last_run_key: str | None = None


def _field_matches(field: str, value: int, minimum: int, maximum: int) -> bool:
    def matches_part(part: str) -> bool:
        base, slash, step_text = part.partition("/")
        try:
            step = int(step_text) if slash else 1
        except ValueError as exc:
            raise ValueError(f"Invalid cron step: {part}") from exc
        if step <= 0:
            raise ValueError("Cron steps must be positive")
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            left, right = base.split("-", 1)
            start, end = int(left), int(right)
        else:
            start = end = int(base)
        if start < minimum or end > maximum or start > end:
            raise ValueError(f"Cron field value is outside {minimum}-{maximum}")
        return start <= value <= end and (value - start) % step == 0

    matched = False
    for part in field.split(","):
        if matches_part(part):
            matched = True
    return matched


def cron_matches(expression: str, when: datetime) -> bool:
    """Match the useful numeric subset of standard five-field cron."""

    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("Cron must contain: minute hour day month weekday")
    minute, hour, day, month, weekday = fields
    cron_weekday = (when.weekday() + 1) % 7
    weekday_matches = _field_matches(weekday, cron_weekday, 0, 7) or (
        cron_weekday == 0 and _field_matches(weekday, 7, 0, 7)
    )
    day_matches = _field_matches(day, when.day, 1, 31)
    calendar_matches = (
        day_matches or weekday_matches
        if not day.startswith("*") and not weekday.startswith("*")
        else day_matches and weekday_matches
    )
    return (
        _field_matches(minute, when.minute, 0, 59)
        and _field_matches(hour, when.hour, 0, 23)
        and _field_matches(month, when.month, 1, 12)
        and calendar_matches
    )


class SQLiteAssistantStore:
    """Single-process durable store for recurring tasks and deferred notices."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._setup()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _setup(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS recurring_tasks (
                    id TEXT PRIMARY KEY,
                    owner_thread_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    cron TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_run_key TEXT,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS deferred_notices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT
                );
                """
            )

    async def create(
        self, *, owner_thread_id: str, prompt: str, cron: str, timezone: str
    ) -> Mapping[str, object]:
        prompt = prompt.strip()
        cron = cron.strip()
        timezone = timezone.strip()
        if not prompt:
            raise ValueError("Recurring task prompt cannot be empty")
        try:
            zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {timezone}") from exc
        cron_matches(cron, datetime.now(zone).replace(second=0, microsecond=0))
        task = RecurringTask(
            id=str(uuid.uuid4()),
            owner_thread_id=owner_thread_id,
            prompt=prompt,
            cron=cron,
            timezone=timezone,
            created_at=datetime.now().astimezone().isoformat(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO recurring_tasks
                    (id, owner_thread_id, prompt, cron, timezone, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.owner_thread_id,
                    task.prompt,
                    task.cron,
                    task.timezone,
                    task.created_at,
                ),
            )
        return asdict(task)

    async def list_for_owner(
        self, owner_thread_id: str
    ) -> tuple[Mapping[str, object], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, owner_thread_id, prompt, cron, timezone, created_at, last_run_key
                FROM recurring_tasks
                WHERE owner_thread_id = ? AND active = 1
                ORDER BY created_at
                """,
                (owner_thread_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    async def claim_due(self, now: datetime) -> tuple[RecurringTask, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, owner_thread_id, prompt, cron, timezone, created_at, last_run_key
                FROM recurring_tasks WHERE active = 1
                """
            ).fetchall()
            due: list[RecurringTask] = []
            for row in rows:
                task = RecurringTask(**dict(row))
                local = now.astimezone(ZoneInfo(task.timezone)).replace(second=0, microsecond=0)
                run_key = local.isoformat()
                if task.last_run_key == run_key or not cron_matches(task.cron, local):
                    continue
                updated = connection.execute(
                    """
                    UPDATE recurring_tasks SET last_run_key = ?
                    WHERE id = ? AND (last_run_key IS NULL OR last_run_key != ?)
                    """,
                    (run_key, task.id, run_key),
                )
                if updated.rowcount:
                    due.append(
                        RecurringTask(
                            **{**asdict(task), "last_run_key": run_key}
                        )
                    )
        return tuple(due)

    async def defer_notice(self, chat_id: str, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO deferred_notices (chat_id, message, created_at)
                VALUES (?, ?, ?)
                """,
                (chat_id, message, datetime.now().astimezone().isoformat()),
            )

    async def pop_notices(self, chat_id: str) -> tuple[str, ...]:
        delivered_at = datetime.now().astimezone().isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, message FROM deferred_notices
                WHERE chat_id = ? AND delivered_at IS NULL ORDER BY id
                """,
                (chat_id,),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"UPDATE deferred_notices SET delivered_at = ? WHERE id IN ({placeholders})",
                    (delivered_at, *ids),
                )
        return tuple(str(row["message"]) for row in rows)


async def scheduler_loop(
    store: SQLiteAssistantStore,
    execute: Callable[[RecurringTask], Awaitable[None]],
    *,
    poll_seconds: float = 30,
) -> None:
    """Claim due tasks once per local cron minute and pass each to the runtime callback."""

    while True:
        now = datetime.now().astimezone()
        for task in await store.claim_due(now):
            try:
                await execute(task)
            except Exception:
                continue
        await asyncio.sleep(poll_seconds)
