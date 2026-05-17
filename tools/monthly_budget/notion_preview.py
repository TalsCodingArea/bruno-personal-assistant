from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date
from contextlib import redirect_stdout
from io import StringIO
from typing import Any, Dict, Optional

from tools.monthly_budget import (
    Month,
    calculate_budget_allocations,
    classify_expenses,
    forecast_monthly_spend,
    predict_monthly_income,
)
from tools.monthly_budget.models import previous_complete_months


def _month_end(month: Month) -> date:
    return month.first_day.replace(day=month.days_in_month)


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


def _fetch_expense_records(start: date, end: date) -> list[dict[str, Any]]:
    with redirect_stdout(StringIO()):
        from tools.notion_tools import get_expenses_between_dates

        result = get_expenses_between_dates.invoke(
            {"start_date": start.isoformat(), "end_date": end.isoformat()}
        )
    records = result.get("records", []) if isinstance(result, dict) else []
    return [
        {
            "Date": record.get("date"),
            "Amount": record.get("amount", 0),
            "Sub Category": record.get("sub_category") or [],
            "Category": record.get("category") or [],
            "Description": record.get("description", ""),
        }
        for record in records
    ]


def _fetch_income_records(start: date, end: date) -> list[dict[str, Any]]:
    with redirect_stdout(StringIO()):
        from tools.notion_tools import get_income_between_dates

        rows = get_income_between_dates.invoke(
            {"start_date": start.isoformat(), "end_date": end.isoformat()}
        )
    return rows if isinstance(rows, list) else []


def build_monthly_budget_preview(
    *,
    target_month: Optional[Month] = None,
    as_of: Optional[date] = None,
    lookback_months: int = 6,
    savings_rate: float = 0.0,
    safety_buffer_rate: float = 0.0,
    fallback_income: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Build a read-only monthly budget preview from Notion data.

    This function only reads expenses/income, then runs the deterministic engine.
    It does not create or update Budget or Financial Summary pages.
    """
    current_date = as_of or date.today()
    month = target_month or Month.from_date(current_date)
    history_months = previous_complete_months(month, lookback_months)
    start = history_months[0].first_day if history_months else month.first_day
    end = current_date

    expenses = _fetch_expense_records(start, end)
    income_rows = _fetch_income_records(start, end)

    income_prediction = predict_monthly_income(
        income_rows,
        month,
        lookback_months=lookback_months,
        fallback_income=fallback_income,
    )
    classifications = classify_expenses(expenses, month, lookback_months=lookback_months)
    forecasts = forecast_monthly_spend(expenses, classifications, month, as_of=current_date)
    allocations = calculate_budget_allocations(
        income_prediction,
        forecasts,
        savings_rate=savings_rate,
        safety_buffer_rate=safety_buffer_rate,
    )

    spendable_budget = round(
        income_prediction.predicted_income
        - (income_prediction.predicted_income * savings_rate)
        - (income_prediction.predicted_income * safety_buffer_rate),
        2,
    )
    allocated_budget = round(sum(item.budget for item in allocations.values()), 2)

    return _serialize(
        {
            "target_month": month,
            "as_of": current_date,
            "source_range": {"start": start, "end": end},
            "records": {
                "expense_count": len(expenses),
                "income_count": len(income_rows),
            },
            "income_prediction": income_prediction,
            "classifications": classifications,
            "forecasts": forecasts,
            "allocations": allocations,
            "totals": {
                "predicted_income": income_prediction.predicted_income,
                "spendable_budget": spendable_budget,
                "allocated_budget": allocated_budget,
                "allocation_gap": round(spendable_budget - allocated_budget, 2),
                "projected_spend": round(
                    sum(item.projected_month_total for item in forecasts.values()),
                    2,
                ),
                "savings_target": round(income_prediction.predicted_income * savings_rate, 2),
                "safety_buffer": round(income_prediction.predicted_income * safety_buffer_rate, 2),
            },
        }
    )


def format_monthly_budget_preview(preview: Dict[str, Any], *, max_rows: int = 20) -> str:
    totals = preview["totals"]
    lines = [
        f"Budget preview for {preview['target_month']['year']}-{preview['target_month']['month']:02d}",
        f"As of: {preview['as_of']}",
        "",
        f"Predicted income: {totals['predicted_income']:,.0f}",
        f"Budget pool: {totals['spendable_budget']:,.0f}",
        f"Projected spend: {totals['projected_spend']:,.0f}",
        f"Allocated budget: {totals['allocated_budget']:,.0f}",
        f"Allocation gap: {totals['allocation_gap']:,.0f}",
    ]
    if totals["savings_target"] or totals["safety_buffer"]:
        lines.append(f"Savings target: {totals['savings_target']:,.0f}")
        lines.append(f"Safety buffer: {totals['safety_buffer']:,.0f}")
    lines.extend(["", "Sub-category allocations:"])

    allocations = preview.get("allocations", {})
    forecasts = preview.get("forecasts", {})
    for name, allocation in sorted(allocations.items(), key=lambda item: -item[1]["budget"])[:max_rows]:
        forecast = forecasts.get(name, {})
        lines.append(
            f"- {name}: budget {allocation['budget']:,.0f} | "
            f"projected {forecast.get('projected_month_total', 0):,.0f} | "
            f"{allocation['classification']}"
        )

    if len(allocations) > max_rows:
        lines.append(f"...and {len(allocations) - max_rows} more")

    return "\n".join(lines)
