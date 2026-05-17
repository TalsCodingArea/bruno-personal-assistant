from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, Optional

from dotenv import load_dotenv
from notion_client import Client

from tools.monthly_budget.models import Month
from tools.monthly_budget.notion_preview import build_monthly_budget_preview

load_dotenv()


@dataclass(frozen=True)
class BudgetPageWrite:
    sub_category: str
    budget: float
    date: date
    financial_summary_page_id: str
    action: str
    page_id: Optional[str] = None
    url: str = ""


@dataclass(frozen=True)
class BudgetPage:
    sub_category: str
    budget: float
    date: date
    page_id: str
    url: str = ""


def _notion_client() -> Client:
    token = os.getenv("NOTION_API_KEY")
    if not token:
        raise ValueError("Missing NOTION_API_KEY environment variable.")
    return Client(auth=token)


def _database_id(env_name: str) -> str:
    value = os.getenv(env_name, "").strip()
    if not value:
        raise ValueError(f"Missing {env_name} environment variable.")
    return value


def _plain_title(title_items: Iterable[Dict[str, Any]]) -> str:
    return "".join(item.get("plain_text", "") for item in title_items)


def _normalize_name(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum() or ch.isspace()).strip()


def _extract_budget_page(page: Dict[str, Any]) -> BudgetPage:
    props = page.get("properties", {})
    title = _plain_title(props.get("Name", {}).get("title", []))
    budget = props.get("Budget", {}).get("number") or 0
    date_value = (props.get("Date", {}).get("date") or {}).get("start")
    return BudgetPage(
        sub_category=title,
        budget=round(float(budget or 0), 2),
        date=date.fromisoformat(date_value[:10]) if date_value else date.min,
        page_id=page["id"],
        url=page.get("url", ""),
    )


def _query_all_pages(client: Client, database_id: str, query_filter: Dict[str, Any]) -> list[Dict[str, Any]]:
    pages: list[Dict[str, Any]] = []
    cursor = None
    while True:
        kwargs: Dict[str, Any] = {"database_id": database_id, "filter": query_filter, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.databases.query(**kwargs)
        pages.extend(response.get("results", []))
        if not response.get("has_more"):
            return pages
        cursor = response.get("next_cursor")


def find_financial_summary_page(
    month: Month,
    *,
    client: Optional[Client] = None,
    database_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Find the Financial Summary page whose Date is the first day of month."""
    notion = client or _notion_client()
    db_id = database_id or _database_id("FINANCIAL_SUMMARY_DATABASE_ID")
    pages = _query_all_pages(
        notion,
        db_id,
        {"property": "Date", "date": {"equals": month.first_day.isoformat()}},
    )
    if not pages:
        raise RuntimeError(f"No Financial Summary page found for {month.first_day.isoformat()}.")
    return pages[0]


def find_budget_page(
    sub_category: str,
    month: Month,
    *,
    client: Optional[Client] = None,
    database_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Find a Budget page by exact Name and Date."""
    notion = client or _notion_client()
    db_id = database_id or _database_id("BUDGET_DATABASE_ID")
    pages = _query_all_pages(
        notion,
        db_id,
        {
            "and": [
                {"property": "Name", "title": {"equals": sub_category}},
                {"property": "Date", "date": {"equals": month.first_day.isoformat()}},
            ]
        },
    )
    return pages[0] if pages else None


def list_budget_pages(
    month: Month,
    *,
    client: Optional[Client] = None,
    database_id: Optional[str] = None,
) -> list[BudgetPage]:
    """List Budget pages for a month."""
    notion = client or _notion_client()
    db_id = database_id or _database_id("BUDGET_DATABASE_ID")
    pages = _query_all_pages(
        notion,
        db_id,
        {"property": "Date", "date": {"equals": month.first_day.isoformat()}},
    )
    budget_pages = [_extract_budget_page(page) for page in pages]
    return sorted(budget_pages, key=lambda page: page.budget, reverse=True)


def find_budget_page_by_name(
    sub_category: str,
    month: Month,
    *,
    client: Optional[Client] = None,
    database_id: Optional[str] = None,
) -> Optional[BudgetPage]:
    """Find a Budget page by exact or emoji-tolerant sub-category name."""
    notion = client or _notion_client()
    db_id = database_id or _database_id("BUDGET_DATABASE_ID")
    exact_page = find_budget_page(sub_category, month, client=notion, database_id=db_id)
    if exact_page:
        return _extract_budget_page(exact_page)

    query = _normalize_name(sub_category)
    if not query:
        return None

    pages = list_budget_pages(month, client=notion, database_id=db_id)
    for page in pages:
        if _normalize_name(page.sub_category) == query:
            return page
    for page in pages:
        normalized = _normalize_name(page.sub_category)
        if query in normalized or normalized in query:
            return page
    return None


def build_budget_page_properties(
    sub_category: str,
    budget: float,
    month: Month,
    financial_summary_page_id: str,
) -> Dict[str, Any]:
    return {
        "Name": {"title": [{"type": "text", "text": {"content": sub_category}}]},
        "Budget": {"number": round(float(budget or 0), 2)},
        "Date": {"date": {"start": month.first_day.isoformat()}},
        "Financial Summary": {"relation": [{"id": financial_summary_page_id}]},
    }


def upsert_budget_page(
    sub_category: str,
    budget: float,
    month: Month,
    financial_summary_page_id: str,
    *,
    dry_run: bool = True,
    client: Optional[Client] = None,
    database_id: Optional[str] = None,
) -> BudgetPageWrite:
    notion = client or _notion_client()
    db_id = database_id or _database_id("BUDGET_DATABASE_ID")
    existing_page = find_budget_page(sub_category, month, client=notion, database_id=db_id)
    properties = build_budget_page_properties(sub_category, budget, month, financial_summary_page_id)

    if dry_run:
        return BudgetPageWrite(
            sub_category=sub_category,
            budget=round(float(budget or 0), 2),
            date=month.first_day,
            financial_summary_page_id=financial_summary_page_id,
            action="update" if existing_page else "create",
            page_id=existing_page.get("id") if existing_page else None,
            url=existing_page.get("url", "") if existing_page else "",
        )

    if existing_page:
        page = notion.pages.update(page_id=existing_page["id"], properties=properties)
        action = "updated"
    else:
        page = notion.pages.create(parent={"database_id": db_id}, properties=properties)
        action = "created"

    return BudgetPageWrite(
        sub_category=sub_category,
        budget=round(float(budget or 0), 2),
        date=month.first_day,
        financial_summary_page_id=financial_summary_page_id,
        action=action,
        page_id=page.get("id"),
        url=page.get("url", ""),
    )


def update_budget_page_amount(
    sub_category: str,
    budget: float,
    month: Month,
    *,
    client: Optional[Client] = None,
    database_id: Optional[str] = None,
) -> BudgetPageWrite:
    """Update the Budget number for an existing monthly Budget page."""
    notion = client or _notion_client()
    db_id = database_id or _database_id("BUDGET_DATABASE_ID")
    existing = find_budget_page_by_name(sub_category, month, client=notion, database_id=db_id)
    if not existing:
        raise RuntimeError(f"No Budget page found for '{sub_category}' in {month.iso()}.")

    page = notion.pages.update(
        page_id=existing.page_id,
        properties={"Budget": {"number": round(float(budget or 0), 2)}},
    )
    return BudgetPageWrite(
        sub_category=existing.sub_category,
        budget=round(float(budget or 0), 2),
        date=month.first_day,
        financial_summary_page_id="",
        action="updated",
        page_id=page.get("id", existing.page_id),
        url=page.get("url", existing.url),
    )


def archive_budget_page(
    sub_category: str,
    month: Month,
    *,
    client: Optional[Client] = None,
    database_id: Optional[str] = None,
) -> BudgetPageWrite:
    """Archive an existing monthly Budget page. This is the Notion equivalent of delete."""
    notion = client or _notion_client()
    db_id = database_id or _database_id("BUDGET_DATABASE_ID")
    existing = find_budget_page_by_name(sub_category, month, client=notion, database_id=db_id)
    if not existing:
        raise RuntimeError(f"No Budget page found for '{sub_category}' in {month.iso()}.")

    page = notion.pages.update(page_id=existing.page_id, archived=True)
    return BudgetPageWrite(
        sub_category=existing.sub_category,
        budget=existing.budget,
        date=month.first_day,
        financial_summary_page_id="",
        action="archived",
        page_id=page.get("id", existing.page_id),
        url=page.get("url", existing.url),
    )


def upsert_monthly_budget_pages_from_preview(
    preview: Dict[str, Any],
    *,
    dry_run: bool = True,
    include_non_predictable: bool = False,
    client: Optional[Client] = None,
) -> Dict[str, Any]:
    notion = client or _notion_client()
    month = Month(preview["target_month"]["year"], preview["target_month"]["month"])
    summary_page = find_financial_summary_page(month, client=notion)
    summary_page_id = summary_page["id"]
    historical_subcategories = set(preview.get("classifications", {}))
    allowed_kinds = {"recurring", "predictable_variable"}
    if include_non_predictable:
        allowed_kinds.add("non_predictable")

    writes = [
        upsert_budget_page(
            sub_category=name,
            budget=allocation["budget"],
            month=month,
            financial_summary_page_id=summary_page_id,
            dry_run=dry_run,
            client=notion,
        )
        for name, allocation in preview.get("allocations", {}).items()
        if float(allocation.get("budget") or 0) > 0
        and name in historical_subcategories
        and preview["classifications"][name].get("kind") in allowed_kinds
    ]

    return {
        "dry_run": dry_run,
        "month": month.iso(),
        "financial_summary_page_id": summary_page_id,
        "financial_summary_url": summary_page.get("url", ""),
        "writes": [write.__dict__ for write in writes],
    }


def build_and_upsert_monthly_budget_pages(
    *,
    target_month: Optional[Month] = None,
    as_of: Optional[date] = None,
    lookback_months: int = 6,
    dry_run: bool = True,
    include_non_predictable: bool = False,
) -> Dict[str, Any]:
    preview = build_monthly_budget_preview(
        target_month=target_month,
        as_of=as_of,
        lookback_months=lookback_months,
    )
    result = upsert_monthly_budget_pages_from_preview(
        preview,
        dry_run=dry_run,
        include_non_predictable=include_non_predictable,
    )
    return {
        "preview": preview,
        "upsert": result,
    }
