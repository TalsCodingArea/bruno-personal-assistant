"""Application use cases combining repositories with deterministic calculations."""

import calendar
from datetime import date
from decimal import Decimal

from app.domain.models import (
    CategorySuggestion,
    CategoryUpdateDraft,
    PlannedExpenseDraft,
    Transaction,
)
from app.domain.money import ZERO, money
from app.services.calculations import (
    MonthEndForecast,
    MonthlyBudgetStatus,
    MonthlyCategorySummary,
    budget_status,
    draft_category_updates,
    draft_planned_expense,
    month_end_forecast,
    monthly_category_summary,
    planned_expense_monthly_allocation,
    suggest_categories,
    uncategorized_review,
)
from app.services.ports import FinanceReader


def month_bounds(month: date) -> tuple[date, date]:
    start = month.replace(day=1)
    return start, date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])


class FinanceQueryService:
    """Fetch only needed data, then delegate all calculations to pure functions."""

    def __init__(self, reader: FinanceReader) -> None:
        self.reader = reader

    async def monthly_summary(self, month: date) -> MonthlyCategorySummary:
        start, end = month_bounds(month)
        transactions = await self.reader.transactions(start, end)
        budgets = await self.reader.budgets(start)
        return monthly_category_summary(transactions, start, budgets=budgets)

    async def uncategorized_transactions(self, month: date) -> tuple[Transaction, ...]:
        start, end = month_bounds(month)
        transactions = await self.reader.transactions(start, end)
        return uncategorized_review(transactions)

    async def category_suggestions(self, month: date) -> tuple[CategorySuggestion, ...]:
        start, end = month_bounds(month)
        history_start = date(start.year - 1, start.month, 1)
        current = await self.reader.transactions(start, end)
        history_end = date.fromordinal(start.toordinal() - 1)
        history = await self.reader.transactions(history_start, history_end)
        return suggest_categories(current, history)

    async def category_update_drafts(self, month: date) -> tuple[CategoryUpdateDraft, ...]:
        return draft_category_updates(await self.category_suggestions(month))

    async def budget_status(self, month: date) -> MonthlyBudgetStatus:
        start, end = month_bounds(month)
        transactions = await self.reader.transactions(start, end)
        budgets = await self.reader.budgets(start)
        return budget_status(transactions, budgets, start)

    async def forecast_month_end(
        self,
        month: date,
        as_of: date,
        *,
        expected_income: Decimal | None = None,
        planned_allocations: Decimal = ZERO,
        buffer: Decimal = ZERO,
    ) -> MonthEndForecast:
        start, end = month_bounds(month)
        transactions = await self.reader.transactions(start, end)
        budgets = await self.reader.budgets(start)
        if expected_income is None:
            incomes = await self.reader.incomes(start, end)
            expected_income = money(sum((item.amount for item in incomes), ZERO))
        return month_end_forecast(
            transactions,
            budgets,
            start,
            as_of,
            expected_income,
            planned_allocations=planned_allocations,
            buffer=buffer,
        )

    async def upcoming_planned_expenses(
        self, today: date, *, limit: int = 20
    ) -> tuple[dict[str, object], ...]:
        end = date(today.year + 5, 12, 31)
        planned = await self.reader.planned_expenses(today, end, limit=limit)
        return tuple(
            {
                "planned_expense": item,
                "allocation": planned_expense_monthly_allocation(item, today),
            }
            for item in planned
        )

    def planned_expense_draft(
        self,
        name: str,
        target_amount: Decimal,
        due_date: date,
        today: date,
        *,
        saved_amount: Decimal = ZERO,
    ) -> PlannedExpenseDraft:
        return draft_planned_expense(
            name,
            target_amount,
            due_date,
            today,
            saved_amount=saved_amount,
        )
