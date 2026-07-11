from __future__ import annotations

import logging
import os
import threading

from personal_assistant.ml.expense_categorizer import dataset, review_queue
from personal_assistant.ml.expense_categorizer.models import (
    CategoryPrediction,
    ReviewItem,
    TrainingExample,
)
from personal_assistant.ml.expense_categorizer.pipeline import ExpenseCategorizerModel

logger = logging.getLogger("personal-assistant.ml.expense-categorizer")

# The model is shared, mutable (feedback retrains it), and touched from both
# the Telegram automation path and agent tools -- hence the lock.
_model_lock = threading.Lock()
_model: ExpenseCategorizerModel | None = None
_model_load_attempted = False


def _get_model() -> ExpenseCategorizerModel | None:
    global _model, _model_load_attempted
    with _model_lock:
        if _model is None and not _model_load_attempted:
            _model_load_attempted = True
            _model = ExpenseCategorizerModel.load()
            if _model is None:
                logger.warning(
                    "No trained expense categorizer found -- expenses will queue "
                    "for review without predictions. Run "
                    "`python scripts/train_expense_categorizer.py` to train one."
                )
        return _model


def _set_model(model: ExpenseCategorizerModel) -> None:
    global _model, _model_load_attempted
    with _model_lock:
        _model = model
        _model_load_attempted = True


# --- training ---------------------------------------------------------------


def ensure_model_trained() -> None:
    """Startup hook: run the initial training if no model exists yet.

    Called once at boot (see app.py), so a fresh container trains itself on
    first run and does nothing on every run after that (the model lives in
    budget_data/ml/, which persists via the compose bind mount). Never raises:
    if Notion is unreachable or there isn't enough labeled data yet, the bot
    still starts and new expenses simply queue for review without predictions.
    """
    from personal_assistant.ml.expense_categorizer.storage import model_path

    if model_path().exists():
        logger.info("Expense categorizer model found at %s -- skipping initial training.", model_path())
        return

    logger.info("No expense categorizer model found -- starting initial training from Notion.")
    try:
        metadata = train_from_notion()
        logger.info("Initial expense categorizer training complete: %s", metadata)
    except Exception:
        logger.exception(
            "Initial expense categorizer training failed -- continuing without a model. "
            "Expenses will queue for review without predictions; run "
            "`python scripts/train_expense_categorizer.py` to retry manually."
        )


def train_from_notion() -> dict:
    """Initial (or refresh) training: pull all labeled Tal expenses from Notion.

    The Notion pull is snapshotted locally so later feedback retrains don't
    need Notion at all. Feedback collected so far is included in the fit.
    """
    examples = dataset.fetch_labeled_expenses_from_notion()
    dataset.save_base_training_set(examples)
    return _retrain_from_local_data()


def _retrain_from_local_data() -> dict:
    examples = dataset.load_all_training_examples()
    model, metadata = ExpenseCategorizerModel.train(examples)
    model.save(metadata)
    _set_model(model)
    return metadata


# --- classification of new expenses ------------------------------------------


def classify_and_enqueue(
    *,
    notion_page_id: str,
    description: str,
    amount: float,
    date: str,
) -> ReviewItem | None:
    """Predict a category for a new uncategorized expense and queue it for review.

    Called from the expense-logging path, so it must never raise: a broken
    model or queue file should not prevent the expense from being logged.
    Returns the queued item, or None if nothing was queued.
    """
    try:
        if review_queue.has_pending_for_page(notion_page_id):
            return None

        model = _get_model()
        prediction: CategoryPrediction | None = None
        if model is not None:
            prediction = model.predict(description, amount, date)

        item = ReviewItem.new(
            notion_page_id=notion_page_id,
            description=description,
            amount=amount,
            date=date,
            prediction=prediction,
        )
        review_queue.add_item(item)
        logger.info(
            "Queued expense for review: %s (%s / %s, confidence=%s)",
            description,
            item.predicted_category,
            item.predicted_sub_category,
            item.confidence,
        )
        return item
    except Exception:
        logger.exception("Failed to classify/queue expense %r", description)
        return None


# --- human-in-the-loop feedback -----------------------------------------------


def apply_review_feedback(
    review_id: str,
    *,
    category: str | None = None,
    sub_category: str | None = None,
) -> ReviewItem:
    """Resolve one review item with the human verdict.

    No category/sub_category arguments means "the prediction was correct".
    Then, in order: update the Notion expense page, log the confirmed label as
    a feedback training example, and retrain the model on everything we have.

    Raises ValueError on unknown/already-resolved items or missing labels, so
    the agent can relay a precise error to the user.
    """
    item = review_queue.get_item(review_id)
    if item is None:
        raise ValueError(f"No review item with id '{review_id}'.")
    if item.status != "pending":
        raise ValueError(f"Review item '{review_id}' was already resolved ({item.status}).")

    final_category = category or item.predicted_category
    final_sub_category = sub_category or item.predicted_sub_category
    if not final_category or not final_sub_category:
        raise ValueError(
            "This item has no model prediction, so both category and "
            "sub_category must be provided explicitly."
        )

    # 1. Notion first: if this fails we raise before touching local state,
    #    keeping Notion and the local queue/training data consistent.
    _update_notion_expense_category(item.notion_page_id, final_category, final_sub_category)

    status = "confirmed" if category is None and sub_category is None else "corrected"
    resolved = review_queue.resolve_item(
        review_id,
        status=status,
        final_category=final_category,
        final_sub_category=final_sub_category,
    )

    dataset.append_feedback_example(
        TrainingExample(
            description=item.description,
            amount=item.amount,
            date=item.date,
            category=final_category,
            sub_category=final_sub_category,
            source="feedback",
        )
    )

    try:
        _retrain_from_local_data()
    except ValueError:
        # Not enough data to train yet -- the feedback is stored and will be
        # picked up by the first real training run.
        logger.info("Feedback stored; skipping retrain (not enough training data yet).")
    except Exception:
        logger.exception("Feedback stored and Notion updated, but retraining failed.")

    return resolved if resolved is not None else item


def dismiss_review_item(review_id: str) -> ReviewItem:
    """Drop an item from the queue without updating Notion or training."""
    item = review_queue.get_item(review_id)
    if item is None:
        raise ValueError(f"No review item with id '{review_id}'.")
    if item.status != "pending":
        raise ValueError(f"Review item '{review_id}' was already resolved ({item.status}).")
    resolved = review_queue.resolve_item(review_id, status="dismissed")
    return resolved if resolved is not None else item


def _update_notion_expense_category(page_id: str, category: str, sub_category: str) -> None:
    from notion_client import Client

    token = os.getenv("NOTION_API_KEY")
    if not token:
        raise ValueError("Missing NOTION_API_KEY environment variable.")

    Client(auth=token).pages.update(
        page_id=page_id,
        properties={
            "Category": {"multi_select": [{"name": category}]},
            "Sub Category": {"multi_select": [{"name": sub_category}]},
        },
    )
