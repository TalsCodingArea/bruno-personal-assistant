from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Iterable

from personal_assistant.tools.monthly_budget.models import ExpenseRecord, Month, normalize_expenses, previous_complete_months

_FIXED_KEYWORDS = (
    "membership",
    "subscription",
    "rent",
    "insurance",
    "mortgage",
    "loan",
    "internet",
    "phone",
    "gym",
)


@dataclass(frozen=True)
class ExpenseClassification:
    sub_category: str
    kind: str
    historical_average: float
    months_present: int
    transaction_count: int
    average_transactions_per_active_month: float
    amount_cv: float
    typical_day: int | None
    confidence: str
    reason: str

    @property
    def is_recurring(self) -> bool:
        return self.kind == "recurring"

    @property
    def is_predictable(self) -> bool:
        return self.kind in {"recurring", "predictable_variable"}


def _coefficient_of_variation(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    if avg == 0:
        return 0.0
    return pstdev(values) / avg


def _typical_day(records: list[ExpenseRecord]) -> int | None:
    if not records:
        return None
    return round(mean(record.date.day for record in records))


def _looks_fixed_by_name(sub_category: str) -> bool:
    normalized = sub_category.lower()
    return any(keyword in normalized for keyword in _FIXED_KEYWORDS)


def classify_expenses(
    expense_records: Iterable[ExpenseRecord | dict],
    target_month: Month,
    *,
    lookback_months: int = 6,
) -> dict[str, ExpenseClassification]:
    """
    Classify sub-categories using history before target_month.

    Kinds:
    - recurring: stable, low-frequency monthly payments.
    - predictable_variable: frequent spending that can be pace-projected.
    - non_predictable: sparse or high-variance spending.
    """
    records = normalize_expenses(expense_records)
    lookback = previous_complete_months(target_month, lookback_months)
    lookback_keys = {month.iso() for month in lookback}

    monthly_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    category_records: dict[str, list[ExpenseRecord]] = defaultdict(list)

    for record in records:
        month_key = Month.from_date(record.date).iso()
        if month_key not in lookback_keys:
            continue
        monthly_totals[record.sub_category][month_key] += record.amount
        category_records[record.sub_category].append(record)

    classifications: dict[str, ExpenseClassification] = {}
    required_recurring_months = max(2, round(lookback_months * 0.5))

    for sub_category, totals_by_month in monthly_totals.items():
        active_totals = [round(total, 2) for total in totals_by_month.values() if total > 0]
        months_present = len(active_totals)
        records_for_category = category_records[sub_category]
        transaction_count = len(records_for_category)
        tx_per_month = transaction_count / months_present if months_present else 0.0
        avg = mean(active_totals) if active_totals else 0.0
        cv = _coefficient_of_variation(active_totals)
        typical_day = _typical_day(records_for_category)

        is_low_frequency_stable = months_present >= required_recurring_months and tx_per_month <= 1.6 and cv <= 0.15
        if is_low_frequency_stable and _looks_fixed_by_name(sub_category):
            kind = "recurring"
            confidence = "high" if months_present >= 4 and cv <= 0.15 else "medium"
            reason = "stable low-frequency monthly charge"
        elif months_present >= 2 and tx_per_month >= 3.0:
            kind = "predictable_variable"
            confidence = "high" if months_present >= 4 else "medium"
            reason = "frequent spending with enough history for pace projection"
        elif months_present >= max(3, required_recurring_months) and cv <= 0.35:
            kind = "predictable_variable"
            confidence = "medium"
            reason = "monthly spend is fairly stable even if transaction count is low"
        else:
            kind = "non_predictable"
            confidence = "low" if months_present <= 1 else "medium"
            reason = "sparse or high-variance spending"

        classifications[sub_category] = ExpenseClassification(
            sub_category=sub_category,
            kind=kind,
            historical_average=round(avg, 2),
            months_present=months_present,
            transaction_count=transaction_count,
            average_transactions_per_active_month=round(tx_per_month, 2),
            amount_cv=round(cv, 3),
            typical_day=typical_day,
            confidence=confidence,
            reason=reason,
        )

    return classifications
