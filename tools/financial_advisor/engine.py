from __future__ import annotations

import calendar
import math
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from tools.financial_advisor.models import (
    AffordabilityResult,
    EmergencyFundResult,
    FutureExpenseReserve,
)


def _amount(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return default
    return default


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return None
    return None


def _sum_amounts(items: list[dict[str, Any]], *keys: str) -> float:
    total = 0.0
    for item in items or []:
        for key in keys:
            if key in item:
                total += _amount(item.get(key))
                break
    return round(total, 2)


def calculate_emergency_fund_target(monthly_budget: float, months: float = 3) -> float:
    return round(max(0.0, _amount(monthly_budget)) * max(0.0, _amount(months, 3.0)), 2)


def evaluate_emergency_fund(balance: float, monthly_budget: float, required_months: float = 3) -> EmergencyFundResult:
    target = calculate_emergency_fund_target(monthly_budget, required_months)
    balance_amount = _amount(balance)
    gap = max(0.0, target - balance_amount)
    surplus = max(0.0, balance_amount - target)
    return EmergencyFundResult(
        target=target,
        balance=round(balance_amount, 2),
        gap=round(gap, 2),
        surplus=round(surplus, 2),
        required_months=required_months,
        status="below_target" if gap > 0 else "at_or_above_target",
    )


def calculate_available_surplus(
    balance: float,
    emergency_target: float,
    upcoming_reserves: float | list[dict[str, Any]] = 0,
) -> float:
    reserves = (
        sum(_amount(item.get("monthly_reserve") or item.get("Monthly Reserve") or item.get("amount")) for item in upcoming_reserves)
        if isinstance(upcoming_reserves, list)
        else _amount(upcoming_reserves)
    )
    return round(max(0.0, _amount(balance) - _amount(emergency_target) - reserves), 2)


def _calendar_months_inclusive(start: date, end: date) -> int:
    if end < start:
        return 1
    return max(1, (end.year - start.year) * 12 + end.month - start.month + 1)


def calculate_future_expense_reserve(future_expense: dict[str, Any], today: date) -> FutureExpenseReserve:
    amount = _amount(future_expense.get("amount") or future_expense.get("Amount"))
    due = _parse_date(
        future_expense.get("month")
        or future_expense.get("due_date")
        or future_expense.get("Month")
        or future_expense.get("Due Date")
    )
    months_remaining = _calendar_months_inclusive(today, due) if due else 1
    monthly_reserve = amount / months_remaining if months_remaining else amount
    return FutureExpenseReserve(
        amount=round(amount, 2),
        months_remaining=months_remaining,
        monthly_reserve=round(monthly_reserve, 2),
        due_date=due.isoformat() if due else None,
    )


def _monthly_budget_total(context: dict[str, Any]) -> float:
    budget = context.get("budget") or context.get("current_budget") or {}
    if isinstance(budget, dict):
        total = _amount(budget.get("total") or budget.get("monthly_budget"))
        if total:
            return total
        categories = budget.get("categories")
        if isinstance(categories, dict):
            return _sum_amounts(list(categories.values()), "budget", "amount")
    if isinstance(budget, list):
        return _sum_amounts(budget, "Budget", "budget", "amount")
    return 0.0


def _latest_balance(context: dict[str, Any]) -> float | None:
    balance = context.get("latest_balance")
    if isinstance(balance, dict):
        value = balance.get("balance") or balance.get("Balance")
        return _amount(value) if value is not None else None
    balances = context.get("balances")
    if isinstance(balances, list) and balances:
        liquid = [item for item in balances if str(item.get("account") or item.get("Account") or "").lower() != "investment"]
        return sum(_amount(item.get("balance") or item.get("Balance")) for item in (liquid or balances))
    if isinstance(balance, (int, float, str)):
        return _amount(balance)
    return None


def _required_reserves(context: dict[str, Any], today: date) -> float:
    return round(
        sum(
            calculate_future_expense_reserve(future_expense, today).monthly_reserve
            for future_expense in context.get("future_expenses", []) or []
        ),
        2,
    )


def evaluate_desire_affordability(desire: dict[str, Any], context: dict[str, Any]) -> AffordabilityResult:
    today = _parse_date(context.get("today")) or date.today()
    cost_raw = desire.get("estimated_cost") or desire.get("Estimated Cost") or desire.get("cost")
    cost = _amount(cost_raw) if cost_raw is not None else None
    balance = _latest_balance(context)
    monthly_budget = _monthly_budget_total(context)
    required_months = _amount(context.get("emergency_fund_months"), 3.0)
    expenses = context.get("expenses") or {}
    spent_so_far = _amount(expenses.get("total")) if isinstance(expenses, dict) else _sum_amounts(expenses, "Final", "Amount", "amount")
    reserves = _required_reserves(context, today)

    missing: list[str] = []
    if cost is None or cost <= 0:
        missing.append("estimated_cost")
    if balance is None:
        missing.append("latest_balance")
    if monthly_budget <= 0:
        missing.append("current_month_budget")

    if missing:
        return AffordabilityResult(
            level="needs_more_info",
            estimated_cost=cost,
            emergency_target=None,
            latest_balance=balance,
            available_after_emergency=None,
            month_remaining_budget=None,
            required_reserves=reserves,
            saving_months=None,
            reasons=[f"Missing {', '.join(missing)}."],
            missing=missing,
        )

    emergency_target = calculate_emergency_fund_target(monthly_budget, required_months)
    available_after_emergency = balance - emergency_target - reserves
    month_remaining_budget = monthly_budget - spent_so_far - reserves
    reasons: list[str] = []

    if cost <= available_after_emergency and cost <= month_remaining_budget:
        level = "affordable_now"
        saving_months = 0
        reasons.append("Purchase keeps the emergency fund and current month budget intact.")
    elif cost > available_after_emergency and balance - cost < emergency_target:
        level = "not_recommended"
        saving_months = math.ceil((cost - max(0, available_after_emergency)) / max(1, monthly_budget * 0.1))
        reasons.append("Purchase would push liquid balance below the emergency fund target.")
    else:
        level = "affordable_with_plan"
        monthly_capacity = max(1.0, max(0.0, month_remaining_budget) or monthly_budget * 0.1)
        saving_months = max(1, math.ceil(cost / monthly_capacity))
        reasons.append("Purchase needs a saving plan before it is smart.")

    if cost > month_remaining_budget:
        reasons.append("Purchase does not fit inside the remaining budget for this month.")
    if reserves > 0:
        reasons.append(f"Upcoming future expenses require {reserves:,.0f} ILS of monthly reserves.")

    return AffordabilityResult(
        level=level,
        estimated_cost=round(cost, 2),
        emergency_target=round(emergency_target, 2),
        latest_balance=round(balance, 2),
        available_after_emergency=round(available_after_emergency, 2),
        month_remaining_budget=round(month_remaining_budget, 2),
        required_reserves=round(reserves, 2),
        saving_months=saving_months,
        reasons=reasons,
        missing=[],
    )


def score_desire(desire: dict[str, Any], affordability_result: AffordabilityResult | dict[str, Any]) -> int:
    result = affordability_result if isinstance(affordability_result, dict) else affordability_result.to_dict()
    strength = int(_amount(desire.get("desire_strength") or desire.get("Desire Strength"), 5))
    necessity = str(desire.get("necessity") or desire.get("Necessity") or "Nice to Have")
    horizon = str(desire.get("time_horizon") or desire.get("Time Horizon") or "Someday")
    created_today = bool(desire.get("created_today"))

    necessity_weight = {"Essential": 8, "Useful": 5, "Nice to Have": 2, "Impulse": -4}.get(necessity, 2)
    horizon_weight = {"Now": 1, "This Month": 3, "1-3 Months": 5, "3-6 Months": 3, "Someday": 1}.get(horizon, 1)
    level = result.get("level")
    affordability_penalty = {
        "not_recommended": -10,
        "affordable_with_plan": -3,
        "affordable_now": 0,
        "needs_more_info": -3,
    }.get(level, -3)
    impulse_penalty = -4 if created_today and necessity in {"Nice to Have", "Impulse"} and horizon == "Now" else 0
    score = strength * 2 + necessity_weight + horizon_weight + affordability_penalty + impulse_penalty
    return max(0, min(100, int(score)))


def project_month_end_spending(
    expenses_so_far: dict[str, Any] | list[dict[str, Any]],
    historical_patterns: dict[str, Any],
    today: date,
) -> dict[str, Any]:
    current = _amount(expenses_so_far.get("total")) if isinstance(expenses_so_far, dict) else _sum_amounts(expenses_so_far, "Final", "Amount", "amount")
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    pace_projection = current / max(1, today.day) * days_in_month
    historical_avg = _amount(historical_patterns.get("monthly_average") or historical_patterns.get("avg"))
    projection = max(pace_projection, historical_avg) if historical_avg else pace_projection
    return {
        "spent_so_far": round(current, 2),
        "projected_month_total": round(projection, 2),
        "basis": "max_of_current_pace_and_history" if historical_avg else "current_pace",
    }
