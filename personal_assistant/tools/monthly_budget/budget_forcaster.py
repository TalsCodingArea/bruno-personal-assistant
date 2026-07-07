from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from personal_assistant.tools.monthly_budget.expense_classifier import ExpenseClassification
from personal_assistant.tools.monthly_budget.models import ExpenseRecord, Month, month_progress, normalize_expenses


@dataclass(frozen=True)
class ForecastResult:
    sub_category: str
    classification: str
    actual_so_far: float
    historical_average: float
    projected_month_total: float
    confidence: str
    reason: str


@dataclass(frozen=True)
class BudgetStatus:
    sub_category: str
    budget: float
    actual_so_far: float
    projected_month_total: float
    month_progress: float
    severity: str
    should_notify: bool
    message: str


def _current_month_actuals(records: list[ExpenseRecord], target_month: Month, as_of: date) -> dict[str, float]:
    actuals: dict[str, float] = defaultdict(float)
    for record in records:
        if target_month.contains(record.date) and record.date <= as_of:
            actuals[record.sub_category] += record.amount
    return {key: round(value, 2) for key, value in actuals.items()}


def forecast_monthly_spend(
    expense_records: Iterable[ExpenseRecord | dict],
    classifications: dict[str, ExpenseClassification],
    target_month: Month,
    *,
    as_of: Optional[date] = None,
) -> dict[str, ForecastResult]:
    """
    Forecast current-month spend by sub-category.

    Recurring categories reserve the historical average even if the charge has
    not happened yet. Predictable variable categories blend current pace and
    history. Non-predictable categories do not extrapolate aggressively.
    """
    current_date = as_of or date.today()
    progress = month_progress(target_month, current_date)
    records = normalize_expenses(expense_records)
    actuals = _current_month_actuals(records, target_month, current_date)

    all_subcategories = set(classifications) | set(actuals)
    forecasts: dict[str, ForecastResult] = {}

    for sub_category in sorted(all_subcategories):
        actual = actuals.get(sub_category, 0.0)
        classification = classifications.get(sub_category)
        kind = classification.kind if classification else "non_predictable"
        historical_average = classification.historical_average if classification else 0.0

        if kind == "recurring":
            projected = max(actual, historical_average)
            reason = "recurring category uses historical monthly amount"
            confidence = classification.confidence if classification else "low"
        elif kind == "predictable_variable":
            pace_projection = actual / progress if progress > 0 else actual
            if historical_average > 0:
                projected = (pace_projection * 0.6) + (historical_average * 0.4)
            else:
                projected = pace_projection
            projected = max(actual, projected)
            reason = "predictable variable category blends current pace with history"
            confidence = classification.confidence if classification else "medium"
        else:
            reserve = historical_average * 0.5 if historical_average > 0 else 0.0
            projected = max(actual, reserve)
            reason = "non-predictable category avoids extrapolating one-off purchases"
            confidence = classification.confidence if classification else "low"

        forecasts[sub_category] = ForecastResult(
            sub_category=sub_category,
            classification=kind,
            actual_so_far=round(actual, 2),
            historical_average=round(historical_average, 2),
            projected_month_total=round(projected, 2),
            confidence=confidence,
            reason=reason,
        )

    return forecasts


def evaluate_budget_status(
    sub_category: str,
    budget: float,
    forecast: ForecastResult,
    target_month: Month,
    *,
    as_of: Optional[date] = None,
    warn_projection_over_pct: float = 0.25,
    warn_pace_multiplier: float = 1.6,
) -> BudgetStatus:
    """Decide whether a logged expense should trigger a budget notification."""
    current_date = as_of or date.today()
    progress = month_progress(target_month, current_date)
    budget = float(budget or 0)
    actual = forecast.actual_so_far
    projected = forecast.projected_month_total

    if budget <= 0:
        return BudgetStatus(
            sub_category=sub_category,
            budget=budget,
            actual_so_far=actual,
            projected_month_total=projected,
            month_progress=round(progress, 3),
            severity="none",
            should_notify=False,
            message="",
        )

    if actual > budget:
        excess = actual - budget
        return BudgetStatus(
            sub_category=sub_category,
            budget=round(budget, 2),
            actual_so_far=actual,
            projected_month_total=projected,
            month_progress=round(progress, 3),
            severity="over_budget",
            should_notify=True,
            message=f"{sub_category} is over budget by {excess:.0f}.",
        )

    projected_over_pct = (projected - budget) / budget
    expected_spend_now = budget * progress
    pace_multiplier = actual / expected_spend_now if expected_spend_now > 0 else 0.0

    if projected_over_pct >= warn_projection_over_pct and pace_multiplier >= warn_pace_multiplier:
        return BudgetStatus(
            sub_category=sub_category,
            budget=round(budget, 2),
            actual_so_far=actual,
            projected_month_total=projected,
            month_progress=round(progress, 3),
            severity="off_track",
            should_notify=True,
            message=(
                f"{sub_category} is pacing high: {actual:.0f} spent vs "
                f"{expected_spend_now:.0f} expected by now."
            ),
        )

    return BudgetStatus(
        sub_category=sub_category,
        budget=round(budget, 2),
        actual_so_far=actual,
        projected_month_total=projected,
        month_progress=round(progress, 3),
        severity="on_track",
        should_notify=False,
        message="",
    )
