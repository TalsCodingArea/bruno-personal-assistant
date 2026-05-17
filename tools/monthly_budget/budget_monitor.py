from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, Optional

from tools.monthly_budget.budget_forcaster import ForecastResult, evaluate_budget_status, forecast_monthly_spend
from tools.monthly_budget.expense_classifier import classify_expenses
from tools.monthly_budget.models import Month, previous_complete_months
from tools.monthly_budget.notion_preview import _fetch_expense_records
from tools.monthly_budget.notion_writer import BudgetPage, find_budget_page_by_name, update_budget_page_amount


@dataclass(frozen=True)
class BudgetEvaluationAlert:
    sub_category: str
    severity: str
    message: str
    budget_before: float
    budget_after: float
    actual_so_far: float
    projected_month_total: float
    updated: bool = False
    url: str = ""


def _extract_subcategories(properties: Dict[str, Any]) -> list[str]:
    raw = properties.get("Sub Category") or properties.get("sub_category") or properties.get("subcategory") or []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def _expense_date(properties: Dict[str, Any]) -> date:
    raw = properties.get("Date") or properties.get("date")
    if isinstance(raw, dict):
        raw = raw.get("start")
    if isinstance(raw, str) and raw:
        return date.fromisoformat(raw[:10])
    return date.today()


def _expense_amount(properties: Dict[str, Any]) -> float:
    raw = properties.get("Amount", properties.get("amount", properties.get("Final", 0))) or 0
    return float(raw)


def _expense_description(properties: Dict[str, Any]) -> str:
    return str(properties.get("Description", properties.get("description", "")) or "")


def _append_logged_expense_if_missing(
    expense_records: list[dict[str, Any]],
    expense_properties: Dict[str, Any],
    subcategories: list[str],
    as_of: date,
) -> list[dict[str, Any]]:
    amount = _expense_amount(expense_properties)
    description = _expense_description(expense_properties)
    for subcategory in subcategories:
        found = any(
            record.get("Date") == as_of.isoformat()
            and float(record.get("Amount") or 0) == amount
            and subcategory in (record.get("Sub Category") or [])
            and (not description or record.get("Description") == description)
            for record in expense_records
        )
        if not found:
            expense_records.append(
                {
                    "Date": as_of.isoformat(),
                    "Amount": amount,
                    "Sub Category": [subcategory],
                    "Category": expense_properties.get("Category") or [],
                    "Description": description,
                }
            )
    return expense_records


def _build_expense_window(target_month: Month, lookback_months: int, as_of: date) -> tuple[date, date]:
    history_months = previous_complete_months(target_month, lookback_months)
    start = history_months[0].first_day if history_months else target_month.first_day
    return start, as_of


def decide_budget_alert(
    budget_page: BudgetPage,
    forecast: ForecastResult,
    target_month: Month,
    *,
    as_of: date,
) -> BudgetEvaluationAlert | None:
    """Return an alert decision for one sub-category, without writing to Notion."""
    status = evaluate_budget_status(
        budget_page.sub_category,
        budget_page.budget,
        forecast,
        target_month,
        as_of=as_of,
    )
    if not status.should_notify:
        return None

    if status.severity == "over_budget":
        new_budget = round(status.actual_so_far, 2)
        return BudgetEvaluationAlert(
            sub_category=budget_page.sub_category,
            severity="over_budget",
            message=(
                f"🚨 {budget_page.sub_category} passed its budget. "
                f"Budget was ₪{budget_page.budget:,.0f}, actual is ₪{status.actual_so_far:,.0f}. "
                f"I updated the budget to ₪{new_budget:,.0f}."
            ),
            budget_before=budget_page.budget,
            budget_after=new_budget,
            actual_so_far=status.actual_so_far,
            projected_month_total=status.projected_month_total,
            updated=True,
            url=budget_page.url,
        )

    if forecast.classification == "predictable_variable":
        return BudgetEvaluationAlert(
            sub_category=budget_page.sub_category,
            severity="critical_projection",
            message=(
                f"⚠️ {budget_page.sub_category} is projected to overflow. "
                f"Budget: ₪{budget_page.budget:,.0f}, actual so far: ₪{status.actual_so_far:,.0f}, "
                f"projected: ₪{status.projected_month_total:,.0f}. "
                "Consider making this budget more conservative or slowing down this category."
            ),
            budget_before=budget_page.budget,
            budget_after=budget_page.budget,
            actual_so_far=status.actual_so_far,
            projected_month_total=status.projected_month_total,
            updated=False,
            url=budget_page.url,
        )

    return None


def evaluate_logged_expense_budget(
    expense_properties: Dict[str, Any],
    *,
    lookback_months: int = 6,
) -> list[BudgetEvaluationAlert]:
    """
    Evaluate Budget status after a logged expense.

    Side effect: if a category is already over budget, update its Budget page to
    the actual current spend. Critical projected overflow only alerts.
    """
    subcategories = _extract_subcategories(expense_properties)
    if not subcategories:
        return []

    as_of = _expense_date(expense_properties)
    target_month = Month.from_date(as_of)
    start, end = _build_expense_window(target_month, lookback_months, as_of)
    expense_records = _fetch_expense_records(start, end)
    expense_records = _append_logged_expense_if_missing(
        expense_records,
        expense_properties,
        subcategories,
        as_of,
    )
    classifications = classify_expenses(expense_records, target_month, lookback_months=lookback_months)
    forecasts = forecast_monthly_spend(expense_records, classifications, target_month, as_of=as_of)

    alerts: list[BudgetEvaluationAlert] = []
    seen: set[str] = set()
    for requested_subcategory in subcategories:
        budget_page = find_budget_page_by_name(requested_subcategory, target_month)
        if not budget_page or budget_page.sub_category in seen:
            continue
        seen.add(budget_page.sub_category)

        forecast = forecasts.get(budget_page.sub_category)
        if not forecast:
            continue

        alert = decide_budget_alert(budget_page, forecast, target_month, as_of=as_of)
        if not alert:
            continue

        if alert.updated:
            updated = update_budget_page_amount(
                budget_page.sub_category,
                alert.budget_after,
                target_month,
            )
            alert = BudgetEvaluationAlert(
                sub_category=alert.sub_category,
                severity=alert.severity,
                message=alert.message,
                budget_before=alert.budget_before,
                budget_after=alert.budget_after,
                actual_so_far=alert.actual_so_far,
                projected_month_total=alert.projected_month_total,
                updated=True,
                url=updated.url or alert.url,
            )
        alerts.append(alert)

    return alerts


def format_budget_alerts(alerts: Iterable[BudgetEvaluationAlert]) -> str:
    messages = []
    for alert in alerts:
        message = alert.message
        if alert.url:
            message += f"\n{alert.url}"
        messages.append(message)
    return "\n\n".join(messages)
