"""Agent tools for the human-in-the-loop expense category review.

Flow: automations log new expenses as "Uncategorized" -> the ML categorizer
(personal_assistant/ml/expense_categorizer) predicts Category / Sub Category
and queues the expense for review -> Tal confirms or corrects through these
tools -> the confirmed label is written to Notion and fed back into the model.

Split of responsibilities:
- Pulling data (Notion fetch, queue sync, status brief) is plain tools here.
- Walking through the suggestions with the user is the human-in-the-loop
  review workflow (agent/general/uncategorized_workflow.py), which builds on
  the same fetch/sync functions.
"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.tools import tool

from personal_assistant.ml.expense_categorizer import review_queue
from personal_assistant.ml.expense_categorizer.models import ReviewItem


def fetch_uncategorized_expenses() -> list[dict[str, Any]]:
    """Pull Tal's uncategorized expenses from the Notion Expenses DB."""
    from personal_assistant.tools.notion_tools import _raw_notion_response_to_dict, notion_get_database_pages

    expenses_database_id = os.getenv("EXPENSES_DATABASE_ID")
    if not expenses_database_id:
        raise ValueError("Missing EXPENSES_DATABASE_ID environment variable.")

    raw = notion_get_database_pages.invoke(
        {
            "database_id": expenses_database_id,
            "filter": {
                "and": [
                    {"property": "Category", "multi_select": {"contains": "Uncategorized"}},
                    {"property": "Tag", "multi_select": {"contains": "Tal 👨🏻"}},
                ]
            },
            "sorts": [{"property": "Date", "direction": "descending"}],
        }
    )
    rows = _raw_notion_response_to_dict(
        ["Description", "Final", "Amount", "Category", "Sub Category", "Date"],
        raw,
    )
    for row in rows:
        row["Amount"] = row.get("Final") or row.get("Amount") or 0
        row.pop("Final", None)
    return rows


def sync_uncategorized_to_review_queue(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sync uncategorized Notion expenses into the ML review queue.

    New expenses normally enter the queue when they're logged; this backfills
    anything older (or logged while the hook was broken) and returns the full
    pending queue.
    """
    from personal_assistant.ml.expense_categorizer.service import classify_and_enqueue

    for transaction in transactions:
        page_id = transaction.get("id") or ""
        if not page_id or review_queue.has_pending_for_page(page_id):
            continue
        classify_and_enqueue(
            notion_page_id=page_id,
            description=str(transaction.get("Description") or ""),
            amount=float(transaction.get("Amount") or 0),
            date=str(transaction.get("Date") or ""),
        )

    return [item.to_dict() for item in review_queue.pending_items()]


@tool
def get_uncategorized_expenses_status() -> str:
    """
    Quick status of uncategorized expenses: how many exist in Notion and how
    many already have an ML category suggestion queued.

    Use this when the user asks WHETHER they have uncategorized expenses or
    wants a status brief. Answer with one short sentence based on the counts
    (e.g. "You have 3 uncategorized expenses, I have suggestions for all of
    them.") — no totals, summaries, or projections. If the user then wants to
    go over the suggestions, start the review workflow
    (start_uncategorized_review).
    """
    transactions = fetch_uncategorized_expenses()
    pending = sync_uncategorized_to_review_queue(transactions)

    total = len(transactions)
    if total == 0 and not pending:
        return "No uncategorized expenses in Notion and the review queue is empty."

    with_suggestion = sum(1 for item in pending if item.get("predicted_sub_category"))
    awaiting_model = len(pending) - with_suggestion
    lines = [
        f"Uncategorized expenses in Notion: {total}",
        f"With a queued ML suggestion: {with_suggestion}",
    ]
    if awaiting_model:
        lines.append(f"Queued without a prediction yet (model not trained): {awaiting_model}")
    return "\n".join(lines)


def format_review_item(item: ReviewItem, index: int | None = None) -> str:
    prefix = f"{index}. " if index is not None else ""
    if item.predicted_sub_category:
        confidence = f"{item.confidence:.0%}" if item.confidence is not None else "?"
        suggestion = f"{item.predicted_category} / {item.predicted_sub_category} ({confidence})"
    else:
        suggestion = "no prediction (model not trained yet)"
    return (
        f"{prefix}[{item.review_id}] {item.date[:10]} | {item.description} | "
        f"₪{item.amount:g} -> {suggestion}"
    )


@tool
def get_pending_expense_reviews() -> str:
    """
    List expenses waiting for category review, with the ML model's suggested
    Category / Sub Category and its confidence.

    Use this when the user asks to review, check, or categorize pending /
    uncategorized expenses. Each line starts with a review id in [brackets] --
    that id is what resolve_expense_review and dismiss_expense_review expect.
    """
    items = review_queue.pending_items()
    if not items:
        return "The expense review queue is empty — nothing waiting for categorization."

    lines = [f"{len(items)} expense(s) waiting for category review:", ""]
    lines += [format_review_item(item, index) for index, item in enumerate(items, start=1)]
    lines += [
        "",
        "Ask the user to confirm or correct each suggestion, then call "
        "resolve_expense_review per item.",
    ]
    return "\n".join(lines)


@tool
def resolve_expense_review(
    review_id: str,
    category: str | None = None,
    sub_category: str | None = None,
) -> str:
    """
    Resolve one pending expense review with the user's verdict.

    If the user confirms the suggestion, call with only review_id.
    If the user corrects it, pass the correct category and/or sub_category
    (use the exact names from the Notion Expenses DB, including emojis).

    This updates the expense page in Notion and retrains the model on the
    confirmed label. Only call this after the user has explicitly confirmed
    or corrected the suggestion — never resolve reviews on your own judgment.
    """
    from personal_assistant.ml.expense_categorizer.service import apply_review_feedback

    try:
        item = apply_review_feedback(review_id, category=category, sub_category=sub_category)
    except ValueError as exc:
        return f"Could not resolve review '{review_id}': {exc}"

    verdict = "confirmed" if item.status == "confirmed" else "corrected"
    return (
        f"Review {verdict}: '{item.description}' is now "
        f"{item.final_category} / {item.final_sub_category}. "
        "Notion is updated and the model has learned from it."
    )


@tool
def dismiss_expense_review(review_id: str) -> str:
    """
    Remove one pending expense review from the queue without categorizing it.

    Use only when the user explicitly says to skip/ignore an item. The expense
    stays "Uncategorized" in Notion and the model does not learn from it.
    """
    from personal_assistant.ml.expense_categorizer.service import dismiss_review_item

    try:
        item = dismiss_review_item(review_id)
    except ValueError as exc:
        return f"Could not dismiss review '{review_id}': {exc}"
    return f"Dismissed: '{item.description}' stays uncategorized in Notion."
