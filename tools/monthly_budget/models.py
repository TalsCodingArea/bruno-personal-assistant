from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Optional


@dataclass(frozen=True, order=True)
class Month:
    year: int
    month: int

    @classmethod
    def from_date(cls, value: date) -> "Month":
        return cls(value.year, value.month)

    @classmethod
    def parse(cls, value: str) -> "Month":
        year_text, month_text = value[:7].split("-")
        return cls(int(year_text), int(month_text))

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def days_in_month(self) -> int:
        return calendar.monthrange(self.year, self.month)[1]

    def contains(self, value: date) -> bool:
        return value.year == self.year and value.month == self.month

    def iso(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


@dataclass(frozen=True)
class ExpenseRecord:
    date: date
    amount: float
    sub_category: str
    category: str = ""
    description: str = ""


@dataclass(frozen=True)
class IncomeRecord:
    date: date
    amount: float
    description: str = ""


def parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value[:10]).date()
    raise ValueError(f"Unsupported date value: {value!r}")


def _first_non_empty(values: Iterable[Any]) -> str:
    for value in values:
        if isinstance(value, list) and value:
            first = value[0]
            if first:
                return str(first)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def expense_from_mapping(row: dict[str, Any]) -> ExpenseRecord:
    amount = row.get("amount", row.get("Amount", row.get("Final", 0))) or 0
    return ExpenseRecord(
        date=parse_date(row.get("date", row.get("Date"))),
        amount=float(amount),
        sub_category=_first_non_empty(
            [
                row.get("sub_category"),
                row.get("Sub Category"),
                row.get("subcategory"),
            ]
        ) or "Uncategorized",
        category=_first_non_empty([row.get("category"), row.get("Category")]),
        description=str(row.get("description", row.get("Description", "")) or ""),
    )


def income_from_mapping(row: dict[str, Any]) -> IncomeRecord:
    amount = row.get("amount", row.get("Amount", 0)) or 0
    return IncomeRecord(
        date=parse_date(row.get("date", row.get("Date"))),
        amount=float(amount),
        description=str(row.get("description", row.get("Description", "")) or ""),
    )


def normalize_expenses(rows: Iterable[ExpenseRecord | dict[str, Any]]) -> list[ExpenseRecord]:
    return [row if isinstance(row, ExpenseRecord) else expense_from_mapping(row) for row in rows]


def normalize_income(rows: Iterable[IncomeRecord | dict[str, Any]]) -> list[IncomeRecord]:
    return [row if isinstance(row, IncomeRecord) else income_from_mapping(row) for row in rows]


def previous_complete_months(target_month: Month, count: int) -> list[Month]:
    months: list[Month] = []
    year = target_month.year
    month = target_month.month
    for _ in range(count):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        months.append(Month(year, month))
    return list(reversed(months))


def month_progress(target_month: Month, as_of: Optional[date] = None) -> float:
    current = as_of or date.today()
    if current < target_month.first_day:
        return 0.0
    if not target_month.contains(current):
        return 1.0
    return max(1 / target_month.days_in_month, current.day / target_month.days_in_month)
