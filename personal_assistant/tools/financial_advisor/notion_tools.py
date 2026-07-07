from __future__ import annotations

from datetime import date
from typing import Any

from langchain_core.tools import tool
from notion_client.errors import APIResponseError

from notion_config.loader import NotionConfigLoader
from personal_assistant.tools.notion_tools import (
    _build_notion_client,
    _extract_notion_property_content,
    get_expenses_between_dates,
    get_income_between_dates,
    get_financial_advisor_habits,
    notion_create_database_page,
    notion_get_database_pages,
    update_financial_advisor_habit,
)
from personal_assistant.tools.financial_advisor.memory import get_current_bank_balance

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
    """Return the locally remembered bank account balance as a balances list."""
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
    from personal_assistant.tools.monthly_budget.agent_tools import review_monthly_budgets

    return {"month": month, "summary": review_monthly_budgets.invoke({"month": month})}


@tool
def get_future_expenses(start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    """Return planned future expenses (Name, Amount, Month) from Notion."""
    raw = notion_get_database_pages.invoke(
        {
            "database_id": _database_id("future_expenses"),
            "filter": _date_filter("Month", start_date, end_date),
            "sorts": [{"property": "Month", "direction": "ascending"}],
        }
    )
    future_expenses = []
    for page in raw.get("results", []):
        item = _page_summary(page, ["Name", "Amount", "Month"])
        future_expenses.append(
            {
                "id": item.get("id"),
                "url": item.get("url"),
                "name": item.get("Name"),
                "amount": item.get("Amount"),
                "month": item.get("Month"),
            }
        )
    return {"future_expenses": future_expenses}


@tool
def create_future_expense(name: str, amount: float, month: str) -> dict[str, Any]:
    """Create a planned future expense in Notion. `month` is an ISO date in the due month."""
    props = _properties(
        Name=("title", name),
        Amount=("number", amount),
        Month=("date", month),
    )
    return notion_create_database_page.invoke(
        {"database_id": _database_id("future_expenses"), "properties": props}
    )


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
        item = _page_summary(page, ["Name", "Budget", "Reason", "Actively Saving"])
        purchases.append(
            {
                "id": item.get("id"),
                "url": item.get("url"),
                "name": item.get("Name"),
                "budget": item.get("Budget"),
                "reason": item.get("Reason"),
                "actively_saving": bool(item.get("Actively Saving")),
            }
        )
    return {"future_purchases": purchases}


@tool
def create_future_purchase(
    name: str,
    budget: float | None = None,
    reason: str = "",
    actively_saving: bool = False,
) -> dict[str, Any]:
    """Create a Future Purchase row in Notion."""
    props = _properties(
        Name=("title", name),
        Budget=("number", budget),
        Reason=("rich_text", reason),
    )
    if actively_saving:
        props["Actively Saving"] = {"type": "checkbox", "content": True}
    return notion_create_database_page.invoke({"database_id": _database_id("future_purchases"), "properties": props})


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
        item = _page_summary(page, ["Country", "Budget", "Recommended Time", "Actively Saving"])
        vacations.append(
            {
                "id": item.get("id"),
                "url": item.get("url"),
                "country": item.get("Country"),
                "budget": item.get("Budget"),
                "recommended_time": item.get("Recommended Time"),
                "actively_saving": bool(item.get("Actively Saving")),
            }
        )
    return {"future_vacations": vacations}


@tool
def create_future_vacation(
    country: str,
    budget: float | None = None,
    recommended_time: str = "",
    actively_saving: bool = False,
) -> dict[str, Any]:
    """Create a Future Vacation row in Notion."""
    props = _properties(
        Country=("title", country),
        Budget=("number", budget),
        **{"Recommended Time": ("rich_text", recommended_time)},
    )
    if actively_saving:
        props["Actively Saving"] = {"type": "checkbox", "content": True}
    return notion_create_database_page.invoke({"database_id": _database_id("future_vacations"), "properties": props})


@tool
def set_actively_saving(database: str, page_id: str, actively_saving: bool) -> dict[str, Any]:
    """Toggle the Actively Saving checkbox on a Future Purchase or Future Vacation page.

    `database` must be 'future_purchases' or 'future_vacations'.
    """
    if database not in {"future_purchases", "future_vacations"}:
        raise ValueError("`database` must be 'future_purchases' or 'future_vacations'.")
    client = _build_notion_client()
    try:
        page = client.pages.update(
            page_id=page_id,
            properties={"Actively Saving": {"checkbox": bool(actively_saving)}},
        )
    except APIResponseError as exc:
        raise RuntimeError(f"Failed to update Actively Saving: {exc}") from exc
    return {"ok": True, "page_id": page.get("id"), "url": page.get("url"), "actively_saving": bool(actively_saving)}


@tool
def update_financial_advisor_rule(rule: str) -> dict[str, Any]:
    """Persist a financial advisor rule or preference."""
    return update_financial_advisor_habit.invoke({"rule": rule})


def load_advisor_rules() -> dict[str, Any]:
    return get_financial_advisor_habits.invoke({})
