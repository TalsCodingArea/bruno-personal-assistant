"""Telegram application construction and lifecycle."""

import logging
from typing import Any

from telegram.ext import Application, MessageHandler, filters

from bruno.config import BrunoSettings, load_bruno_settings
from bruno.runtime import BrunoRuntime
from bruno.telegram_bot.routing import route_document, route_text

logger = logging.getLogger("bruno")
TelegramApplication = Application[Any, Any, Any, Any, Any, Any]


def create_application(
    settings: BrunoSettings | None = None,
) -> TelegramApplication:
    resolved = settings or load_bruno_settings()
    runtime = BrunoRuntime(resolved)

    async def post_init(_: TelegramApplication) -> None:
        await runtime.start()

    async def post_shutdown(_: TelegramApplication) -> None:
        await runtime.aclose()

    application = (
        Application.builder()
        .token(resolved.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.bot_data["settings"] = resolved
    application.bot_data["runtime"] = runtime
    application.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, route_text))
    application.add_handler(MessageHandler(filters.Document.ALL, route_document))
    return application


def run_bot() -> None:
    application = create_application()
    logger.info("Starting Bruno Telegram shell")
    application.run_polling()
