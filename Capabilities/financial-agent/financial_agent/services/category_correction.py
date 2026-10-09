"""User-directed category corrections with optional durable merchant learning."""

from datetime import date, timedelta

from financial_agent.services.category_memory import CategoryMemory
from financial_agent.services.expense_classification import (
    ExpenseClassificationReader,
    ExpenseClassificationWriter,
)


class CategoryCorrectionService:
    def __init__(
        self,
        reader: ExpenseClassificationReader,
        writer: ExpenseClassificationWriter,
        memory: CategoryMemory,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.memory = memory

    async def context(self, start_date: date, end_date: date) -> dict[str, object]:
        if start_date > end_date or (end_date - start_date).days > 366:
            raise ValueError("Choose an expense date range of at most one year")
        expenses = await self.reader.transactions(start_date, end_date)
        history = await self.reader.transactions(end_date - timedelta(days=730), end_date)
        pairs = sorted(
            {
                (row.category, row.subcategory)
                for row in history
                if row.category and row.subcategory and row.category.casefold() != "uncategorized"
            }
        )
        return {"expenses": expenses, "category_pairs": pairs}

    async def correct(
        self,
        *,
        page_id: str,
        category: str,
        subcategory: str,
        expected_category: tuple[str, ...],
        expected_subcategory: tuple[str, ...],
        remember: bool,
        rationale: str,
    ) -> dict[str, object]:
        category, subcategory = category.strip(), subcategory.strip()
        if not category or not subcategory or not rationale.strip():
            raise ValueError("Category, subcategory, and rationale are required")
        current = await self.reader.transaction(page_id)
        if current is None:
            raise ValueError("Expense not found")
        before = (current.category_options, current.subcategory_options)
        target = ((category,), (subcategory,))
        if before != target and before != (expected_category, expected_subcategory):
            raise ValueError("Expense categories changed; reload context before correcting")
        history = await self.reader.transactions(date.min, date.max)
        if not any(row.category == category and row.subcategory == subcategory for row in history):
            raise ValueError("Choose an existing category/subcategory pair from expense context")
        # Prepare memory first, but never save it before a verified expense write.
        draft = None
        memory_error = None
        if remember:
            try:
                draft = await self.memory.draft(
                    current.description, category, subcategory, rationale
                )
            except Exception:
                memory_error = "Merchant memory is unavailable. Retry to remember this correction."
        if before != target:
            await self.writer.apply_classification(page_id, category, subcategory)
        confirmed = await self.reader.transaction(page_id)
        if (
            confirmed is None
            or (confirmed.category_options, confirmed.subcategory_options) != target
        ):
            raise RuntimeError("Expense correction could not be verified; memory was not saved")
        if draft is not None:
            try:
                await self.memory.save(draft)
            except Exception:
                memory_error = (
                    "Expense corrected, but saving the merchant rule failed. Retry to remember."
                )
        return {
            "page_id": page_id,
            "applied": True,
            "before": before,
            "category": category,
            "subcategory": subcategory,
            "remembered": draft is not None and memory_error is None,
            "memory_error": memory_error,
        }
