"""Best-effort delivery to Bruno's configured logs chat."""

import logging

from telegram.ext import ContextTypes

from bruno.config import BrunoSettings

logger = logging.getLogger("bruno.telegram")


async def safe_log(
    context: ContextTypes.DEFAULT_TYPE,
    settings: BrunoSettings,
    text: str,
) -> None:
    if not settings.channels.logs:
        logger.info(text)
        return
    try:
        await context.bot.send_message(chat_id=settings.channels.logs, text=text)
    except Exception:
        logger.exception("Failed to deliver Bruno log message")
