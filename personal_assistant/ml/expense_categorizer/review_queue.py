from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from personal_assistant.ml.expense_categorizer.models import ReviewItem
from personal_assistant.ml.expense_categorizer.storage import review_queue_path

logger = logging.getLogger("personal-assistant.ml.expense-categorizer")


def _load_items() -> list[ReviewItem]:
    path = review_queue_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "[]")
        return [ReviewItem.from_dict(item) for item in raw]
    except (OSError, json.JSONDecodeError, TypeError):
        logger.exception("Failed to read the expense review queue at %s", path)
        return []


def _save_items(items: list[ReviewItem]) -> None:
    review_queue_path().write_text(
        json.dumps([item.to_dict() for item in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_item(item: ReviewItem) -> None:
    items = _load_items()
    items.append(item)
    _save_items(items)


def pending_items() -> list[ReviewItem]:
    return [item for item in _load_items() if item.status == "pending"]


def unnotified_pending_items() -> list[ReviewItem]:
    return [item for item in pending_items() if item.notified_at is None]


def has_pending_for_page(notion_page_id: str) -> bool:
    return any(
        item.notion_page_id == notion_page_id and item.status == "pending"
        for item in _load_items()
    )


def get_item(review_id: str) -> ReviewItem | None:
    return next((item for item in _load_items() if item.review_id == review_id), None)


def mark_notified(review_ids: list[str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    items = _load_items()
    for item in items:
        if item.review_id in review_ids:
            item.notified_at = now
    _save_items(items)


def resolve_item(
    review_id: str,
    *,
    status: str,
    final_category: str | None = None,
    final_sub_category: str | None = None,
) -> ReviewItem | None:
    """Mark an item confirmed/corrected/dismissed; returns the updated item."""
    items = _load_items()
    resolved: ReviewItem | None = None
    for item in items:
        if item.review_id == review_id:
            item.status = status
            item.resolved_at = datetime.now(timezone.utc).isoformat()
            item.final_category = final_category
            item.final_sub_category = final_sub_category
            resolved = item
            break
    if resolved is not None:
        _save_items(items)
    return resolved
