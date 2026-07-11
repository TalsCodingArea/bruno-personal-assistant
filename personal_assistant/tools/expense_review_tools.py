"""Agent tools for the human-in-the-loop expense category review.

Flow: automations log new expenses as "Uncategorized" -> the ML categorizer
(personal_assistant/ml/expense_categorizer) predicts Category / Sub Category
and queues the expense for review -> Tal confirms or corrects through these
tools -> the confirmed label is written to Notion and fed back into the model.
"""

from __future__ import annotations

from langchain_core.tools import tool

from personal_assistant.ml.expense_categorizer import review_queue
from personal_assistant.ml.expense_categorizer.models import ReviewItem


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
