"""Telegram application construction and lifecycle."""

import logging
from typing import Any

from telegram.ext import Application, ContextTypes, MessageHandler, filters

from bruno.config import BrunoSettings, load_bruno_settings
from bruno.runtime import BrunoRuntime
from bruno.telegram_bot.logging import safe_log
from bruno.telegram_bot.routing import route_document, route_text

logger = logging.getLogger("bruno")
TelegramApplication = Application[Any, Any, Any, Any, Any, Any]


async def handle_update_error(
    _: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Log an update failure without allowing one bad message to stop polling."""

    error = context.error
    if isinstance(error, BaseException):
        logger.error(
            "Unhandled Telegram update error",
            exc_info=(type(error), error, error.__traceback__),
        )
    else:
        logger.error("Unhandled Telegram update error: %r", error)
    settings = context.application.bot_data.get("settings")
    if isinstance(settings, BrunoSettings):
        await safe_log(
            context,
            settings,
            f"[telegram:error] {type(error).__name__}: {error}",
        )


def create_application(
    settings: BrunoSettings | None = None,
) -> TelegramApplication:
    resolved = settings or load_bruno_settings()
    runtime = BrunoRuntime(resolved)

    async def post_init(application: TelegramApplication) -> None:
        await runtime.start()

        async def send_message(chat_id: str, text: str) -> None:
            await application.bot.send_message(chat_id=chat_id, text=text)

        await runtime.start_scheduler(send_message)

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
    application.add_error_handler(handle_update_error)
    return application


def run_bot() -> None:
    application = create_application()
    logger.info("Starting Bruno Telegram shell")
    application.run_polling(bootstrap_retries=5)
