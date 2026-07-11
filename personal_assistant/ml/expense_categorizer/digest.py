"""Builds the morning review-digest text.

Deliberately platform-free: this module knows what to say, the Telegram
reminders handler knows how to deliver it. Swapping Telegram for another
platform never touches this file.
"""

from __future__ import annotations

from personal_assistant.ml.expense_categorizer import review_queue


def build_review_digest() -> tuple[str | None, list[str]]:
    """Return (message, review_ids covered) -- message is None when quiet.

    The digest covers only items never mentioned before (notified_at is null),
    but shows the total pending count so nothing feels forgotten.
    """
    from personal_assistant.tools.expense_review_tools import format_review_item

    new_items = review_queue.unnotified_pending_items()
    if not new_items:
        return None, []

    total_pending = len(review_queue.pending_items())
    lines = [
        f"🏷️ Good morning! {len(new_items)} new expense(s) need a category review:",
        "",
    ]
    lines += [format_review_item(item, index) for index, item in enumerate(new_items, start=1)]
    if total_pending > len(new_items):
        lines.append("")
        lines.append(f"(Plus {total_pending - len(new_items)} older item(s) still waiting.)")
    lines.append("")
    lines.append('Reply "review expenses" and we\'ll go through them together.')
    return "\n".join(lines), [item.review_id for item in new_items]
