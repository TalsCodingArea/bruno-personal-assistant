from __future__ import annotations

import logging

from telegram.ext import Application, MessageHandler, filters

from personal_assistant.config import settings
from personal_assistant.telegram.routing import route_document, route_text

logger = logging.getLogger("telegram-assistant")


def create_application() -> Application:
    application = Application.builder().token(settings.bot_token).build()
    application.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, route_text))
    application.add_handler(MessageHandler(filters.Document.ALL, route_document))
    return application


def run_bot() -> None:
    application = create_application()
    logger.info(
        "Starting Telegram bot. Configured chat IDs: %s",
        {k: bool(v) for k, v in settings.channels.as_dict().items()},
    )
    application.run_polling()
