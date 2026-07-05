from __future__ import annotations

from datetime import date, datetime
from typing import Any

from langchain_core.tools import tool

from notion_config.loader import NotionConfigLoader
from tools.notion_tools import (
    _extract_notion_property_content,
    get_expenses_between_dates,
    get_income_between_dates,
    get_financial_advisor_habits,
    notion_create_database_page,
    notion_get_database_pages,
    update_financial_advisor_habit,
)
from tools.financial_advisor.memory import (
    get_current_bank_balance,
    update_bank_account_balance,
)

_loader = NotionConfigLoader()


def _database_id(logical_name: str) -> str:
    database_id = _loader.get_database_id(logical_name)
    if database_id.startswith("YOUR_"):
        raise ValueError(f"Database '{logical_name}' is not configured with a real Notion database id.")
    return database_id


def _prop(page: dict[str, Any], name: str) -> Any:
    return _extract_notion_property_content(page.get("properties", {}).get(name, {}))


def _page_summary(page: dict[str, Any], property_names: list[str]) -> dict[str, Any]:
    item = {"id": page.get("id"), "url": page.get("url")}
    for name in property_names:
        item[name] = _prop(page, name)
    return item


def _date_filter(property_name: str, start_date: str | None, end_date: str | None) -> dict[str, Any] | None:
    filters = []
    if start_date:
        filters.append({"property": property_name, "date": {"on_or_after": start_date}})
    if end_date:
        filters.append({"property": property_name, "date": {"on_or_before": end_date}})
    if not filters:
        return None
    return {"and": filters} if len(filters) > 1 else filters[0]


def _normalize_date(raw: str | None) -> str:
    if raw:
        return raw
    return date.today().isoformat()


def _properties(**items: tuple[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        name: {"type": prop_type, "content": value}
        for name, (prop_type, value) in items.items()
        if value is not None and value != ""
    }


@tool
def get_expense_summary(start_date: str, end_date: str) -> dict[str, Any]:
    """Return expense totals and category breakdown for a date range."""
    return get_expenses_between_dates.invoke({"start_date": start_date, "end_date": end_date})


@tool
def get_transactions(
    start_date: str,
    end_date: str,
    category: str | None = None,
    sub_category: str | None = None,
    min_amount: float | None = None,
) -> dict[str, Any]:
    """Return filtered expense transactions for a date range."""
    summary = get_expenses_between_dates.invoke({"start_date": start_date, "end_date": end_date})
    records = summary.get("records", [])
    filtered = []
    for record in records:
        categories = record.get("category") or []
        sub_categories = record.get("sub_category") or []
        amount = float(record.get("amount") or 0)
        if category and category not in categories:
            continue
        if sub_category and sub_category not in sub_categories:
            continue
        if min_amount is not None and amount < min_amount:
            continue
        filtered.append(record)
    return {"period": summary.get("period"), "count": len(filtered), "records": filtered}


@tool
def get_income_summary(start_date: str, end_date: str) -> dict[str, Any]:
    """Return income records and total income for a date range."""
    records = get_income_between_dates.invoke({"start_date": start_date, "end_date": end_date})
    total = sum(float(record.get("Amount") or record.get("amount") or 0) for record in records)
    return {"period": {"start": start_date, "end": end_date}, "total": round(total, 2), "records": records}


@tool
def get_latest_account_balances(account: str | None = None) -> dict[str, Any]:
    """Return the locally remembered bank account balance in the legacy balances shape."""
    balance = get_current_bank_balance.invoke({})
    if balance.get("balance") is None:
        return {"balances": []}
    return {
        "balances": [
            {
                "account": account or "Main Checking",
                "balance": balance.get("balance"),
                "currency": balance.get("currency", "ILS"),
                "date": balance.get("updated_at"),
                "source": "Local Memory",
                "notes": balance.get("notes", ""),
            }
        ]
    }


@tool
def get_current_budget(month: str = "") -> dict[str, Any]:
    """Return current monthly budget pages if the budget database is configured."""
    from tools.monthly_budget.agent_tools import review_monthly_budgets

    return {"month": month, "summary": review_monthly_budgets.invoke({"month": month})}


@tool
def get_future_obligations(
    start_date: str | None = None,
    end_date: str | None = None,
    status: str = "Active",
) -> dict[str, Any]:
    """Return future financial obligations from Notion."""
    filters = []
    if status:
        filters.append({"property": "Status", "select": {"equals": status}})
    date_filter = _date_filter("Due Date", start_date, end_date)
    if date_filter:
        if "and" in date_filter:
            filters.extend(date_filter["and"])
        else:
            filters.append(date_filter)
    query_filter = {"and": filters} if len(filters) > 1 else (filters[0] if filters else None)
    raw = notion_get_database_pages.invoke(
        {
            "database_id": _database_id("future_financial_obligations"),
            "filter": query_filter,
            "sorts": [{"property": "Due Date", "direction": "ascending"}],
        }
    )
    obligations = []
    for page in raw.get("results", []):
        item = _page_summary(
            page,
            [
                "Name",
                "Amount",
                "Due Date",
                "Recurrence",
                "Category",
                "Importance",
                "Reserve Start",
                "Monthly Reserve",
                "Status",
                "Notes",
                "Last Reviewed",
            ],
        )
        obligations.append(
            {
                "id": item.get("id"),
                "url": item.get("url"),
                "name": item.get("Name"),
                "amount": item.get("Amount"),
                "due_date": item.get("Due Date"),
                "recurrence": item.get("Recurrence"),
                "category": item.get("Category"),
                "importance": item.get("Importance"),
                "reserve_start": item.get("Reserve Start"),
                "monthly_reserve": item.get("Monthly Reserve"),
                "status": item.get("Status"),
                "notes": item.get("Notes"),
                "last_reviewed": item.get("Last Reviewed"),
            }
        )
    return {"obligations": obligations}


@tool
def get_future_purchases(min_budget: float | None = None) -> dict[str, Any]:
    """Return current Future Purchases from Notion."""
    query_filter = None
    if min_budget is not None:
        query_filter = {"property": "Budget", "number": {"greater_than_or_equal_to": min_budget}}
    raw = notion_get_database_pages.invoke(
        {
            "database_id": _database_id("future_purchases"),
            "filter": query_filter,
            "sorts": [{"property": "Budget", "direction": "descending"}],
        }
    )
    purchases = []
    for page in raw.get("results", []):
        item = _page_summary(page, ["Name", "Budget", "Priority", "Reason", "Notes", "URL", "Tag"])
        purchases.append(
            {
                "id": item.get("id"),
                "url": item.get("url"),
                "name": item.get("Name"),
                "budget": item.get("Budget"),
                "estimated_cost": item.get("Budget"),
                "priority": item.get("Priority"),
                "reason": item.get("Reason"),
                "notes": item.get("Notes"),
                "product_url": item.get("URL"),
                "tags": item.get("Tag") or [],
            }
        )
    return {"future_purchases": purchases}


@tool
def get_financial_desires(status: str | None = None, min_priority: float | None = None) -> dict[str, Any]:
    """Compatibility alias: return Future Purchases as financial desires."""
    purchases = get_future_purchases.invoke({"min_budget": None})
    return {"desires": purchases.get("future_purchases", [])}


@tool
def get_future_vacations() -> dict[str, Any]:
    """Return current Future Vacations from Notion."""
    raw = notion_get_database_pages.invoke(
        {
            "database_id": _database_id("future_vacations"),
            "sorts": [{"property": "Budget", "direction": "descending"}],
        }
    )
    vacations = []
    for page in raw.get("results", []):
        item = _page_summary(page, ["Country", "Budget", "Travel Dates", "Activities"])
        vacations.append(
            {
                "id": item.get("id"),
                "url": item.get("url"),
                "country": item.get("Country"),
                "budget": item.get("Budget"),
                "travel_dates": item.get("Travel Dates"),
                "activities": item.get("Activities") or [],
            }
        )
    return {"future_vacations": vacations}


@tool
def create_financial_desire(
    name: str,
    estimated_cost: float | None = None,
    category: str = "Other",
    desire_strength: int = 5,
    necessity: str = "Nice to Have",
    time_horizon: str = "Someday",
    target_date: str | None = None,
    reason: str = "",
    advisor_notes: str = "",
    priority_score: float | None = None,
    status: str = "Captured",
) -> dict[str, Any]:
    """Compatibility alias: create a Future Purchase row."""
    return create_future_purchase.invoke(
        {
            "name": name,
            "budget": estimated_cost,
            "reason": reason,
            "notes": advisor_notes,
            "tags": [category] if category else [],
            "high_priority": bool(priority_score and priority_score >= 20),
        }
    )


@tool
def create_future_purchase(
    name: str,
    budget: float | None = None,
    reason: str = "",
    notes: str = "",
    url: str = "",
    tags: list[str] | None = None,
    high_priority: bool = False,
) -> dict[str, Any]:
    """Create a Future Purchase row in Notion."""
    props = _properties(
        Name=("title", name),
        Budget=("number", budget),
        Reason=("rich_text", reason),
        Notes=("rich_text", notes),
        URL=("url", url),
        Tag=("multi_select", tags or []),
    )
    if high_priority:
        props["Priority"] = {"type": "select", "content": "🚩"}
    return notion_create_database_page.invoke({"database_id": _database_id("future_purchases"), "properties": props})


@tool
def create_future_vacation(
    country: str,
    budget: float | None = None,
    travel_start: str | None = None,
    travel_end: str | None = None,
    activities: list[str] | None = None,
) -> dict[str, Any]:
    """Create a Future Vacation row in Notion."""
    travel_dates = None
    if travel_start:
        travel_dates = {"start": travel_start, "end": travel_end}
    props = _properties(
        Country=("title", country),
        Budget=("number", budget),
        **{
            "Travel Dates": ("date", travel_dates),
            "Activities": ("multi_select", activities or []),
        },
    )
    return notion_create_database_page.invoke({"database_id": _database_id("future_vacations"), "properties": props})


@tool
def update_financial_desire_status(desire_id: str, status: str, notes: str | None = None) -> dict[str, Any]:
    """Return an update plan for a financial desire status change."""
    return {
        "requires_confirmation": True,
        "action": "update_financial_desire_status",
        "desire_id": desire_id,
        "status": status,
        "notes": notes,
    }


@tool
def create_future_obligation(
    name: str,
    amount: float,
    due_date: str,
    recurrence: str = "One Time",
    category: str = "Other",
    importance: str = "Mandatory",
    reserve_start: str | None = None,
    monthly_reserve: float | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Create a future financial obligation in Notion."""
    today = date.today().isoformat()
    props = _properties(
        Name=("title", name),
        Amount=("number", amount),
        **{
            "Due Date": ("date", due_date),
            "Recurrence": ("select", recurrence),
            "Category": ("select", category),
            "Importance": ("select", importance),
            "Reserve Start": ("date", reserve_start),
            "Monthly Reserve": ("number", monthly_reserve),
            "Status": ("select", "Active"),
            "Notes": ("rich_text", notes),
            "Last Reviewed": ("date", today),
        },
    )
    return notion_create_database_page.invoke(
        {"database_id": _database_id("future_financial_obligations"), "properties": props}
    )


@tool
def update_future_obligation(obligation_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """Return an update plan for a future obligation change."""
    return {
        "requires_confirmation": True,
        "action": "update_future_obligation",
        "obligation_id": obligation_id,
        "updates": updates,
    }


@tool
def create_balance_snapshot(
    account: str,
    balance: float,
    currency: str = "ILS",
    date: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Compatibility alias: update locally remembered bank balance."""
    return update_bank_account_balance.invoke({"balance": balance, "currency": currency, "notes": notes})


@tool
def log_financial_recommendation(
    name: str,
    recommendation_type: str,
    recommendation: str,
    numbers_used: str = "",
    status: str = "Suggested",
) -> dict[str, Any]:
    """Log a financial recommendation in Notion if the optional database is configured."""
    props = _properties(
        Name=("title", name),
        Date=("date", datetime.now().date().isoformat()),
        Type=("select", recommendation_type),
        Recommendation=("rich_text", recommendation),
        **{
            "Numbers Used": ("rich_text", numbers_used),
            "Status": ("select", status),
        },
    )
    return notion_create_database_page.invoke(
        {"database_id": _database_id("financial_recommendations"), "properties": props}
    )


@tool
def update_financial_advisor_rule(rule: str) -> dict[str, Any]:
    """Persist a financial advisor rule or preference."""
    return update_financial_advisor_habit.invoke({"rule": rule})


def load_advisor_rules() -> dict[str, Any]:
    return get_financial_advisor_habits.invoke({})
