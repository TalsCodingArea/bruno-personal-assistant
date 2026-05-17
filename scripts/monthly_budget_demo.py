from __future__ import annotations

import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.monthly_budget import (
    Month,
    calculate_budget_allocations,
    classify_expenses,
    forecast_monthly_spend,
    predict_monthly_income,
)


def _expense(day: date, amount: float, sub_category: str) -> dict:
    return {
        "Date": day.isoformat(),
        "Amount": amount,
        "Sub Category": [sub_category],
        "Description": sub_category,
    }


def _income(day: date, amount: float) -> dict:
    return {"Date": day.isoformat(), "Amount": amount, "Description": "Salary"}


def _json_default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value)!r}")


def main() -> None:
    target_month = Month(2026, 5)
    expenses = []
    for month in (1, 2, 3, 4):
        expenses.append(_expense(date(2026, month, 3), 220, "Gym Membership"))
        for day in (2, 8, 14, 21, 27):
            expenses.append(_expense(date(2026, month, day), 120 + month, "Groceries"))
        expenses.append(_expense(date(2026, month, 16), 350 + (month * 20), "Restaurants"))
    expenses.extend(
        [
            _expense(date(2026, 2, 10), 900, "Home Goods"),
            _expense(date(2026, 4, 22), 1200, "Home Goods"),
            _expense(date(2026, 5, 3), 220, "Gym Membership"),
            _expense(date(2026, 5, 5), 180, "Groceries"),
            _expense(date(2026, 5, 9), 190, "Groceries"),
            _expense(date(2026, 5, 10), 800, "Restaurants"),
        ]
    )
    income = [
        _income(date(2026, 1, 10), 12000),
        _income(date(2026, 2, 10), 12100),
        _income(date(2026, 3, 10), 11900),
        _income(date(2026, 4, 10), 12200),
    ]

    income_prediction = predict_monthly_income(income, target_month)
    classifications = classify_expenses(expenses, target_month, lookback_months=4)
    forecasts = forecast_monthly_spend(expenses, classifications, target_month, as_of=date(2026, 5, 10))
    allocations = calculate_budget_allocations(income_prediction, forecasts)

    print(
        json.dumps(
            {
                "income_prediction": income_prediction,
                "classifications": classifications,
                "forecasts": forecasts,
                "allocations": allocations,
            },
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )
    )


if __name__ == "__main__":
    main()
