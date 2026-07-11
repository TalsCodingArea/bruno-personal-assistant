from __future__ import annotations

import datetime
import logging
import os
from zoneinfo import ZoneInfo

from telegram.ext import Application, MessageHandler, filters

from personal_assistant.config import settings
from personal_assistant.telegram.routing import route_document, route_text

logger = logging.getLogger("telegram-assistant")


def _register_scheduled_jobs(application: Application) -> None:
    """Register recurring jobs (requires python-telegram-bot[job-queue])."""
    if application.job_queue is None:
        logger.warning(
            "JobQueue unavailable -- install python-telegram-bot[job-queue] to "
            "enable the morning expense review digest."
        )
        return

    from personal_assistant.telegram.handlers.reminders import send_expense_review_digest

    digest_hour = int(os.getenv("EXPENSE_REVIEW_DIGEST_HOUR", "8"))
    timezone = ZoneInfo(os.getenv("ASSISTANT_TIMEZONE", "Asia/Jerusalem"))
    application.job_queue.run_daily(
        send_expense_review_digest,
        time=datetime.time(hour=digest_hour, tzinfo=timezone),
        name="expense_review_digest",
    )
    logger.info("Scheduled expense review digest daily at %02d:00 %s.", digest_hour, timezone)


def create_application() -> Application:
    application = Application.builder().token(settings.bot_token).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, route_text))
    application.add_handler(MessageHandler(filters.Document.ALL, route_document))
    _register_scheduled_jobs(application)
    return application


def run_bot() -> None:
    application = create_application()
    logger.info(
        "Starting Telegram bot. Configured chat IDs: %s",
        {k: bool(v) for k, v in settings.channels.as_dict().items()},
    )
    application.run_polling()
