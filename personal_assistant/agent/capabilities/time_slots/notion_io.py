"""Notion I/O for the time-slot rename workflow.

All Notion reads/writes live here so graph.py stays pure orchestration and
tests can inject fakes. Property names match the Time Slots / Uni Tasks /
Courses schemas:

- Time Slots:  Name (title), Tasks (text), Uni Tasks (relation), Date (date)
- Uni Tasks:   Name (title), Status (status), Courses (relation)
- Courses:     Name (title)
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from typing import Any

logger = logging.getLogger("personal-assistant.time-slots")

_PAGE_ID_PATTERN = re.compile(r"([0-9a-f]{32})", re.IGNORECASE)


def parse_page_id(url: str) -> str:
    """Extract the page id from any Notion page URL form.

    Handles https://www.notion.so/Title-<32hex>, https://app.notion.com/p/<32hex>,
    already-dashed UUIDs, and trailing query strings. Raises ValueError when no
    id is present.
    """
    candidate = (url or "").strip().replace("-", "")
    match = _PAGE_ID_PATTERN.search(candidate)
    if not match:
        raise ValueError(f"Could not find a Notion page id in: {url!r}")
    raw = match.group(1).lower()
    return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


def _client():
    from notion_client import Client

    token = os.getenv("NOTION_API_KEY")
    if not token:
        raise ValueError("Missing NOTION_API_KEY environment variable.")
    return Client(auth=token)


def _title_text(properties: dict[str, Any], name: str = "Name") -> str:
    parts = properties.get(name, {}).get("title", [])
    return "".join(part.get("plain_text", "") for part in parts).strip()


def _rich_text(properties: dict[str, Any], name: str) -> str:
    parts = properties.get(name, {}).get("rich_text", [])
    return "".join(part.get("plain_text", "") for part in parts).strip()


def _relation_ids(properties: dict[str, Any], name: str) -> list[str]:
    return [item["id"] for item in properties.get(name, {}).get("relation", [])]


def fetch_time_slot(page_id: str) -> dict[str, Any]:
    """Return {'id', 'name', 'tasks_text', 'task_ids', 'date'} for a slot page."""
    page = _client().pages.retrieve(page_id=page_id)
    properties = page.get("properties", {})
    date_info = properties.get("Date", {}).get("date") or {}
    return {
        "id": page["id"],
        "name": _title_text(properties),
        "tasks_text": _rich_text(properties, "Tasks"),
        "task_ids": _relation_ids(properties, "Uni Tasks"),
        "date": date_info.get("start") or "",
    }


def fetch_task(task_id: str) -> dict[str, Any]:
    """Return {'name', 'status', 'course'} for a Uni Task page."""
    client = _client()
    page = client.pages.retrieve(page_id=task_id)
    properties = page.get("properties", {})

    course = ""
    course_ids = _relation_ids(properties, "Courses")
    if course_ids:
        course = _fetch_page_title(course_ids[0])

    status_info = properties.get("Status", {}).get("status") or {}
    return {
        "name": _title_text(properties),
        "status": status_info.get("name", ""),
        "course": course,
    }


@lru_cache(maxsize=128)
def _fetch_page_title(page_id: str) -> str:
    """Course pages barely change, so titles are cached per process."""
    try:
        page = _client().pages.retrieve(page_id=page_id)
        return _title_text(page.get("properties", {}))
    except Exception:
        logger.exception("Failed to fetch page title for %s", page_id)
        return ""


def fetch_recent_named_slots(limit: int = 8) -> list[dict[str, str]]:
    """Recent slots that already carry a real name -- few-shot style examples.

    Filters out rows still named by the template default ("Time Slots") and
    empty task lists, so the LLM only sees examples of the target style.
    Returns [] (with a log) on any failure: examples improve naming but are
    not required for the workflow to run.
    """
    database_id = os.getenv("TIME_SLOTS_DATABASE_ID")
    if not database_id:
        logger.warning("TIME_SLOTS_DATABASE_ID not set -- skipping naming examples.")
        return []

    try:
        response = _client().databases.query(
            database_id=database_id,
            sorts=[{"property": "Date", "direction": "descending"}],
            page_size=50,
        )
    except Exception:
        logger.exception("Failed to query Time Slots for naming examples.")
        return []

    examples: list[dict[str, str]] = []
    for page in response.get("results", []):
        properties = page.get("properties", {})
        name = _title_text(properties)
        tasks_text = _rich_text(properties, "Tasks")
        if not name or name == "Time Slots" or not tasks_text:
            continue
        examples.append({"name": name, "tasks": tasks_text})
        if len(examples) >= limit:
            break
    return examples


def update_time_slot_name(page_id: str, new_name: str) -> None:
    _client().pages.update(
        page_id=page_id,
        properties={"Name": {"title": [{"type": "text", "text": {"content": new_name}}]}},
    )
