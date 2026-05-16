from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"
DEFAULT_USER_NAME = "Tal"
DEFAULT_TIMEZONE = "Asia/Jerusalem"

NUTRITION_LOGS_DATA_SOURCE_ID = os.getenv(
    "NUTRITION_LOGS_DATA_SOURCE_ID", ""
)
MACROS_GOAL_DATA_SOURCE_ID = os.getenv(
    "MACROS_GOAL_DATA_SOURCE_ID", ""
)

MACRO_FIELDS = ("Calories", "Protein", "Carbs", "Fats")


def _notion_headers() -> Dict[str, str]:
    token = os.getenv("NOTION_API_KEY")
    if not token:
        raise ValueError("Missing NOTION_API_KEY environment variable.")
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _query_data_source(data_source_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Query a Notion data source.

    The current Notion API uses /data_sources. The /databases fallback keeps this
    helper compatible with older notion-client/API behavior around collection IDs.
    """
    if not data_source_id:
        raise ValueError("Missing Notion data source ID environment variable.")

    endpoints = (
        f"{NOTION_API_BASE}/data_sources/{data_source_id}/query",
        f"{NOTION_API_BASE}/databases/{data_source_id}/query",
    )
    last_error: Optional[requests.HTTPError] = None

    for endpoint in endpoints:
        response = requests.post(endpoint, headers=_notion_headers(), json=payload, timeout=20)
        if response.status_code < 400:
            return response.json()
        last_error = requests.HTTPError(f"{response.status_code}: {response.text}", response=response)

    if last_error:
        raise last_error
    raise RuntimeError("Notion query failed without a response.")


def _extract_title(property_data: Dict[str, Any]) -> str:
    return "".join(part.get("plain_text", "") for part in property_data.get("title", []))


def _extract_number(property_data: Dict[str, Any]) -> float:
    value = property_data.get("number")
    return float(value or 0)


def _extract_date(property_data: Dict[str, Any]) -> Optional[str]:
    date_data = property_data.get("date")
    if not date_data:
        return None
    return date_data.get("start")


def _extract_select(property_data: Dict[str, Any]) -> Optional[str]:
    select_data = property_data.get("select")
    if not select_data:
        return None
    return select_data.get("name")


def _extract_macros(properties: Dict[str, Any]) -> Dict[str, float]:
    return {field.lower(): _extract_number(properties.get(field, {})) for field in MACRO_FIELDS}


def _normalize_nutrition_log_page(page: Dict[str, Any]) -> Dict[str, Any]:
    properties = page.get("properties", {})
    return {
        "id": page.get("id"),
        "url": page.get("url"),
        "name": _extract_title(properties.get("Name", {})),
        "date": _extract_date(properties.get("Date", {})),
        "tag": _extract_select(properties.get("Tag", {})),
        **_extract_macros(properties),
    }


def _normalize_macro_goal_page(page: Dict[str, Any]) -> Dict[str, Any]:
    properties = page.get("properties", {})
    return {
        "id": page.get("id"),
        "url": page.get("url"),
        "name": _extract_title(properties.get("Name", {})),
        **_extract_macros(properties),
    }


def _today_iso(timezone: str = DEFAULT_TIMEZONE) -> str:
    return datetime.now(ZoneInfo(timezone)).date().isoformat()


def get_today_nutrition_log(
    user_name: str = DEFAULT_USER_NAME,
    *,
    date_iso: Optional[str] = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> Dict[str, Any]:
    """
    Fetch and normalize today's consumed macros from the Nutrition Logs database.

    Returns a dict with `consumed` macro totals and the raw matching `records`.
    If no row exists for today, consumed macros are returned as zeroes.
    """
    target_date = date_iso or _today_iso(timezone)
    payload = {
        "filter": {
            "and": [
                {"property": "Date", "date": {"equals": target_date}},
                {"property": "Tag", "select": {"equals": user_name}},
            ]
        },
        "page_size": 10,
    }

    response = _query_data_source(NUTRITION_LOGS_DATA_SOURCE_ID, payload)
    records = [_normalize_nutrition_log_page(page) for page in response.get("results", [])]

    consumed = {field.lower(): 0.0 for field in MACRO_FIELDS}
    for record in records:
        for field in consumed:
            consumed[field] += float(record.get(field) or 0)

    return {
        "user": user_name,
        "date": target_date,
        "consumed": consumed,
        "records": records,
    }


def get_macro_goal(user_name: str = DEFAULT_USER_NAME) -> Dict[str, Any]:
    """Fetch and normalize a user's macro goal from the Macros database."""
    payload = {
        "filter": {
            "property": "Name",
            "title": {"equals": user_name},
        },
        "page_size": 1,
    }

    response = _query_data_source(MACROS_GOAL_DATA_SOURCE_ID, payload)
    results = response.get("results", [])
    if not results:
        raise ValueError(f"No macro goal found for user '{user_name}'.")

    goal = _normalize_macro_goal_page(results[0])
    return {
        "user": user_name,
        "goal": {field.lower(): float(goal.get(field.lower()) or 0) for field in MACRO_FIELDS},
        "record": goal,
    }


def get_nutrition_snapshot(
    user_name: str = DEFAULT_USER_NAME,
    *,
    date_iso: Optional[str] = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> Dict[str, Any]:
    """Return consumed, goal, and remaining macros for a user/date."""
    log = get_today_nutrition_log(user_name, date_iso=date_iso, timezone=timezone)
    goal = get_macro_goal(user_name)

    remaining = {
        field: goal["goal"][field] - log["consumed"][field]
        for field in (macro.lower() for macro in MACRO_FIELDS)
    }

    return {
        "user": user_name,
        "date": log["date"],
        "timezone": timezone,
        "consumed": log["consumed"],
        "goal": goal["goal"],
        "remaining": remaining,
        "log_records": log["records"],
        "goal_record": goal["record"],
    }
