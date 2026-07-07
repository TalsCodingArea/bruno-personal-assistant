from __future__ import annotations

from dataclasses import dataclass

from personal_assistant.tools.monthly_budget.budget_forcaster import ForecastResult
from personal_assistant.tools.monthly_budget.income_predictor import IncomePrediction


@dataclass(frozen=True)
class BudgetAllocation:
    sub_category: str
    budget: float
    classification: str
    base_amount: float
    variable_pool_share: float
    reason: str


def calculate_budget_allocations(
    income_prediction: IncomePrediction,
    forecasts: dict[str, ForecastResult],
    *,
    savings_rate: float = 0.0,
    safety_buffer_rate: float = 0.0,
) -> dict[str, BudgetAllocation]:
    """
    Allocate monthly income into per-sub-category budgets.

    Recurring and predictable categories are funded first. Optional savings and
    safety-buffer rates are supported, but default to zero because the budget
    pool is the full predicted income unless the caller explicitly says otherwise.
    Remaining money becomes the variable pool for non-predictable categories,
    weighted by historical/current projected demand.
    """
    income = income_prediction.predicted_income
    savings_target = max(0.0, income * savings_rate)
    safety_buffer = max(0.0, income * safety_buffer_rate)
    spendable = max(0.0, income - savings_target - safety_buffer)

    recurring: dict[str, ForecastResult] = {
        name: forecast
        for name, forecast in forecasts.items()
        if forecast.classification == "recurring"
    }
    predictable: dict[str, ForecastResult] = {
        name: forecast
        for name, forecast in forecasts.items()
        if forecast.classification == "predictable_variable"
    }
    variable: dict[str, ForecastResult] = {
        name: forecast
        for name, forecast in forecasts.items()
        if forecast.classification == "non_predictable"
    }

    recurring_total = sum(forecast.projected_month_total for forecast in recurring.values())
    recurring_scale = min(1.0, spendable / recurring_total) if recurring_total > 0 else 1.0
    recurring_allocated_total = recurring_total * recurring_scale

    remaining_after_recurring = max(0.0, spendable - recurring_allocated_total)
    predictable_total = sum(forecast.projected_month_total for forecast in predictable.values())
    predictable_scale = (
        min(1.0, remaining_after_recurring / predictable_total)
        if predictable_total > 0
        else 1.0
    )
    predictable_allocated_total = predictable_total * predictable_scale

    variable_pool = max(0.0, remaining_after_recurring - predictable_allocated_total)
    variable_weight_total = sum(
        max(forecast.projected_month_total, forecast.historical_average, 1.0)
        for forecast in variable.values()
    )

    allocations: dict[str, BudgetAllocation] = {}

    for name, forecast in recurring.items():
        budget = forecast.projected_month_total * recurring_scale
        allocations[name] = BudgetAllocation(
            sub_category=name,
            budget=round(budget, 2),
            classification=forecast.classification,
            base_amount=round(forecast.projected_month_total, 2),
            variable_pool_share=0.0,
            reason="funded first because it is recurring",
        )

    for name, forecast in predictable.items():
        budget = forecast.projected_month_total * predictable_scale
        allocations[name] = BudgetAllocation(
            sub_category=name,
            budget=round(budget, 2),
            classification=forecast.classification,
            base_amount=round(forecast.projected_month_total, 2),
            variable_pool_share=0.0,
            reason="funded after recurring expenses because it is predictable",
        )

    for name, forecast in variable.items():
        weight = max(forecast.projected_month_total, forecast.historical_average, 1.0)
        pool_share = variable_pool * (weight / variable_weight_total) if variable_weight_total else 0.0
        base = max(forecast.actual_so_far, min(forecast.historical_average * 0.5, pool_share))
        cap = max(base, forecast.projected_month_total, forecast.historical_average)
        assigned_pool_share = min(pool_share, cap)
        budget = max(base, assigned_pool_share)
        allocations[name] = BudgetAllocation(
            sub_category=name,
            budget=round(budget, 2),
            classification=forecast.classification,
            base_amount=round(base, 2),
            variable_pool_share=round(assigned_pool_share, 2),
            reason="allocated from remaining variable pool",
        )

    return dict(sorted(allocations.items(), key=lambda item: item[0].lower()))
