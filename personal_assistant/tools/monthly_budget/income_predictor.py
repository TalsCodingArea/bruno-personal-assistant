from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, median
from typing import Iterable, Optional

from personal_assistant.tools.monthly_budget.models import IncomeRecord, Month, normalize_income, previous_complete_months


@dataclass(frozen=True)
class IncomePrediction:
    target_month: Month
    predicted_income: float
    current_month_income: float
    historical_monthly_totals: dict[str, float]
    method: str
    confidence: str


def _monthly_totals(records: list[IncomeRecord]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for record in records:
        month = Month.from_date(record.date).iso()
        totals[month] = round(totals.get(month, 0.0) + record.amount, 2)
    return totals


def predict_monthly_income(
    income_records: Iterable[IncomeRecord | dict],
    target_month: Month,
    *,
    lookback_months: int = 6,
    fallback_income: Optional[float] = None,
) -> IncomePrediction:
    """
    Predict target-month income before all income rows have been logged.

    If target month already has income, return the greater of current logged
    income and the historical estimate. Otherwise use a robust median/average
    blend over previous complete months.
    """
    records = normalize_income(income_records)
    totals = _monthly_totals(records)
    target_key = target_month.iso()
    current_income = totals.get(target_key, 0.0)

    lookback_keys = [month.iso() for month in previous_complete_months(target_month, lookback_months)]
    history = {key: totals.get(key, 0.0) for key in lookback_keys if totals.get(key, 0.0) > 0}
    values = list(history.values())

    if values:
        historical_estimate = (median(values) * 0.7) + (mean(values) * 0.3)
        confidence = "high" if len(values) >= 4 else "medium"
        method = "historical_median_mean_blend"
    elif fallback_income is not None:
        historical_estimate = float(fallback_income)
        confidence = "low"
        method = "fallback_income"
    else:
        historical_estimate = 0.0
        confidence = "low"
        method = "no_income_history"

    predicted = max(current_income, historical_estimate)
    if current_income > 0 and current_income >= historical_estimate * 0.9:
        method = "current_month_logged_income"
        confidence = "high"

    return IncomePrediction(
        target_month=target_month,
        predicted_income=round(predicted, 2),
        current_month_income=round(current_income, 2),
        historical_monthly_totals=history,
        method=method,
        confidence=confidence,
    )
