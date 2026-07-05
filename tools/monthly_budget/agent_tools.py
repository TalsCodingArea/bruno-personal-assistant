from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from tools.monthly_budget.models import Month
from tools.monthly_budget.notion_preview import build_monthly_budget_preview, format_monthly_budget_preview
from tools.monthly_budget.notion_writer import (
    archive_budget_page,
    build_and_upsert_monthly_budget_pages,
    find_financial_summary_page,
    list_budget_pages,
    upsert_budget_page,
    update_budget_page_amount,
)
from tools.notion_tools import get_expenses_between_dates

DEFAULT_TIMEZONE = "Asia/Jerusalem"


def _resolve_month(month: str = "") -> Month:
    if month:
        return Month.parse(month)
    return Month.from_date(datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date())


def _next_month(value: Month) -> Month:
    if value.month == 12:
        return Month(value.year + 1, 1)
    return Month(value.year, value.month + 1)


def _resolve_budget_creation_month(month: str = "") -> Month:
    if month:
        return Month.parse(month)
    return _next_month(Month.from_date(datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()))


def _parse_as_of(as_of: str = "") -> date:
    if as_of:
        return date.fromisoformat(as_of[:10])
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()


def _month_end(month: Month) -> date:
    return month.first_day + timedelta(days=month.days_in_month - 1)


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
def preview_monthly_budget_plan(
    month: str = "",
    lookback_months: int = 6,
    savings_rate: float = 0.0,
    safety_buffer_rate: float = 0.0,
    fallback_income: float | None = None,
) -> str:
    """
    Preview a proposed monthly Budget plan without writing to Notion.

    Args:
        month: Optional target month YYYY-MM. Defaults to next month.
        lookback_months: Number of previous months to learn spending patterns from.
        savings_rate: Optional share of predicted income to reserve before budgeting, e.g. 0.1.
        safety_buffer_rate: Optional share of predicted income to keep unallocated, e.g. 0.05.
        fallback_income: Optional fallback monthly income if income history is sparse.
    """
    if lookback_months < 1:
        raise ValueError("lookback_months must be positive.")
    target_month = _resolve_budget_creation_month(month)
    preview = build_monthly_budget_preview(
        target_month=target_month,
        as_of=_parse_as_of(),
        lookback_months=lookback_months,
        savings_rate=savings_rate,
        safety_buffer_rate=safety_buffer_rate,
        fallback_income=fallback_income,
    )
    lines = [
        format_monthly_budget_preview(preview),
        "",
        "No Notion changes were made.",
        "After Tal approves, call apply_monthly_budget_plan with the same parameters.",
    ]
    return "\n".join(lines)


@tool
def apply_monthly_budget_plan(
    month: str = "",
    lookback_months: int = 6,
    include_non_predictable: bool = False,
    savings_rate: float = 0.0,
    safety_buffer_rate: float = 0.0,
    fallback_income: float | None = None,
    approved: bool = False,
) -> str:
    """
    Create or update monthly Budget rows in Notion from the deterministic preview.

    Use only after Tal explicitly approves the preview. Defaults to next month.
    approved: Must be true after explicit approval, otherwise no write is performed.
    """
    if not approved:
        return (
            "No Budget rows were changed. Preview the plan first, ask Tal for approval, "
            "then call apply_monthly_budget_plan with approved=True."
        )
    if lookback_months < 1:
        raise ValueError("lookback_months must be positive.")
    target_month = _resolve_budget_creation_month(month)
    result = build_and_upsert_monthly_budget_pages(
        target_month=target_month,
        as_of=_parse_as_of(),
        lookback_months=lookback_months,
        dry_run=False,
        include_non_predictable=include_non_predictable,
        savings_rate=savings_rate,
        safety_buffer_rate=safety_buffer_rate,
        fallback_income=fallback_income,
    )
    writes = result["upsert"]["writes"]
    lines = [
        f"Applied Budget plan for {target_month.iso()}.",
        f"Rows created/updated: {len(writes)}",
    ]
    summary_url = result["upsert"].get("financial_summary_url")
    if summary_url:
        lines.append(summary_url)
    for write in writes[:20]:
        lines.append(f"- {write['action']}: {write['sub_category']} -> ₪{write['budget']:,.0f}")
    if len(writes) > 20:
        lines.append(f"...and {len(writes) - 20} more")
    return "\n".join(lines)


@tool
def review_monthly_budget_status(month: str = "", as_of: str = "") -> str:
    """
    Compare Budget rows to actual spending for a month.

    Args:
        month: Optional YYYY-MM. Defaults to current month.
        as_of: Optional YYYY-MM-DD cutoff date. Defaults to today.
    """
    target_month = _resolve_month(month)
    cutoff = _parse_as_of(as_of)
    if cutoff < target_month.first_day:
        cutoff = target_month.first_day
    if cutoff > _month_end(target_month):
        cutoff = _month_end(target_month)

    pages = list_budget_pages(target_month)
    if not pages:
        return f"No Budget pages found for {target_month.iso()}."

    expenses = get_expenses_between_dates.invoke(
        {"start_date": target_month.first_day.isoformat(), "end_date": cutoff.isoformat()}
    )
    spent_by_subcategory = expenses.get("by_subcategory", {}) if isinstance(expenses, dict) else {}
    elapsed_fraction = max(1 / target_month.days_in_month, cutoff.day / target_month.days_in_month)

    rows = []
    for page in pages:
        spent = float(spent_by_subcategory.get(page.sub_category, 0) or 0)
        projected = spent / elapsed_fraction
        remaining = page.budget - spent
        if spent > page.budget:
            status = "over budget"
        elif projected > page.budget:
            status = "projected over"
        else:
            status = "on track"
        rows.append(
            {
                "sub_category": page.sub_category,
                "budget": page.budget,
                "spent": spent,
                "projected": projected,
                "remaining": remaining,
                "status": status,
                "url": page.url,
            }
        )

    total_budget = sum(row["budget"] for row in rows)
    total_spent = sum(row["spent"] for row in rows)
    total_projected = sum(row["projected"] for row in rows)
    priority_rows = sorted(
        rows,
        key=lambda row: (
            row["status"] != "over budget",
            row["status"] != "projected over",
            -row["projected"] / row["budget"] if row["budget"] else 0,
        ),
    )

    lines = [
        f"Budget status for {target_month.iso()} as of {cutoff.isoformat()}",
        f"Total budget: ₪{total_budget:,.0f}",
        f"Spent so far: ₪{total_spent:,.0f}",
        f"Projected month end: ₪{total_projected:,.0f}",
        "",
    ]
    for row in priority_rows[:20]:
        lines.append(
            f"- {row['sub_category']}: {row['status']} | "
            f"spent ₪{row['spent']:,.0f} / budget ₪{row['budget']:,.0f} | "
            f"projected ₪{row['projected']:,.0f}"
        )
    if len(priority_rows) > 20:
        lines.append(f"...and {len(priority_rows) - 20} more")
    return "\n".join(lines)


@tool
def set_monthly_budget(sub_category: str, budget: float, month: str = "") -> str:
    """
    Create or update one Budget row for a month.

    Use after Tal explicitly asks to set/adjust a category budget.
    The row name should match the expense sub-category name.
    """
    if not sub_category or not sub_category.strip():
        raise ValueError("sub_category is required.")
    if budget < 0:
        raise ValueError("budget must be non-negative.")

    target_month = _resolve_month(month)
    summary_page = find_financial_summary_page(target_month)
    result = upsert_budget_page(
        sub_category.strip(),
        budget,
        target_month,
        summary_page["id"],
        dry_run=False,
    )
    response = f"{result.action.title()} {result.sub_category} for {target_month.iso()} at ₪{result.budget:,.0f}."
    if result.url:
        response += f"\n{result.url}"
    return response


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
