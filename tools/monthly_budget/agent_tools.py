from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from tools.monthly_budget.models import Month
from tools.monthly_budget.notion_writer import (
    archive_budget_page,
    list_budget_pages,
    update_budget_page_amount,
)

DEFAULT_TIMEZONE = "Asia/Jerusalem"


def _resolve_month(month: str = "") -> Month:
    if month:
        return Month.parse(month)
    return Month.from_date(datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date())


@tool
def review_monthly_budgets(month: str = "") -> str:
    """
    Review Budget database pages for a month.

    Args:
        month: Optional YYYY-MM month. Defaults to the current month.
    """
    target_month = _resolve_month(month)
    pages = list_budget_pages(target_month)
    if not pages:
        return f"No Budget pages found for {target_month.iso()}."

    total = sum(page.budget for page in pages)
    lines = [
        f"Budget pages for {target_month.iso()}",
        f"Total budget: ₪{total:,.0f}",
        "",
    ]
    for page in pages:
        lines.append(f"- {page.sub_category}: ₪{page.budget:,.0f}")
    return "\n".join(lines)


@tool
def update_monthly_budget(sub_category: str, budget: float, month: str = "") -> str:
    """
    Update a single Budget page amount.

    Args:
        sub_category: Budget page name or close text match. Emojis are optional.
        budget: New budget amount in ILS.
        month: Optional YYYY-MM month. Defaults to the current month.
    """
    if not sub_category or not sub_category.strip():
        raise ValueError("sub_category is required.")
    if budget < 0:
        raise ValueError("budget must be non-negative.")

    target_month = _resolve_month(month)
    result = update_budget_page_amount(sub_category.strip(), budget, target_month)
    response = (
        f"Updated {result.sub_category} for {target_month.iso()} to ₪{result.budget:,.0f}."
    )
    if result.url:
        response += f"\n{result.url}"
    return response


@tool
def delete_monthly_budget(sub_category: str, month: str = "") -> str:
    """
    Delete/archive a single Budget page.

    Args:
        sub_category: Budget page name or close text match. Emojis are optional.
        month: Optional YYYY-MM month. Defaults to the current month.
    """
    if not sub_category or not sub_category.strip():
        raise ValueError("sub_category is required.")

    target_month = _resolve_month(month)
    result = archive_budget_page(sub_category.strip(), target_month)
    return f"Archived {result.sub_category} for {target_month.iso()}."
