"""Future expense-trigger boundary; no polling or webhook transport is selected yet."""

from collections.abc import Awaitable, Callable
from typing import Protocol

from app.domain.expense_monitoring import ExpenseChangedEvent

ExpenseEventHandler = Callable[[ExpenseChangedEvent], Awaitable[None]]


class ExpenseTrigger(Protocol):
    """Transport contract for a future Notion webhook or polling adapter."""

    async def start(self, handler: ExpenseEventHandler) -> None: ...


class UnconfiguredExpenseTrigger:
    """Explicit stub preventing accidental claims that live ingestion exists."""

    async def start(self, handler: ExpenseEventHandler) -> None:
        del handler
        raise RuntimeError(
            "Expense trigger transport is not configured; invoke expense_monitor directly."
        )
