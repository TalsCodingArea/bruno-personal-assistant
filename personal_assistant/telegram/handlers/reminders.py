"""Scheduled Telegram reminders.

Currently one job: a morning digest of expenses waiting for category review.
Expenses queue up silently during the day (see
personal_assistant/ml/expense_categorizer); each morning Bruno sends one
batched message for everything that hasn't been mentioned yet, so a busy
shopping day doesn't turn into a stream of pings.

This module is delivery only -- the digest text itself is built by the
platform-free personal_assistant/ml/expense_categorizer/digest.py.
"""

from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from personal_assistant.config import settings

logger = logging.getLogger("telegram-assistant")


async def send_expense_review_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    """python-telegram-bot JobQueue callback -- must never raise."""
    try:
        chat_id = settings.channels.personal_assistant
        if not chat_id:
            logger.warning("Expense review digest skipped: no personal assistant chat id configured.")
            return

        from personal_assistant.ml.expense_categorizer import review_queue
        from personal_assistant.ml.expense_categorizer.digest import build_review_digest

        message, review_ids = build_review_digest()
        if message is None:
            return

        await context.bot.send_message(chat_id=chat_id, text=message)
        review_queue.mark_notified(review_ids)
        logger.info("Sent expense review digest covering %d item(s).", len(review_ids))
    except Exception:
        logger.exception("Failed to send the expense review digest.")
