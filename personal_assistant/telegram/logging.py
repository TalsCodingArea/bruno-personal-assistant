from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from personal_assistant.config import settings

logger = logging.getLogger("telegram-assistant")


async def safe_log(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Send a best-effort project log message to the configured logs channel."""
    logs_chat = settings.channels.logs
    if not logs_chat:
        return
    try:
        await context.bot.send_message(chat_id=int(logs_chat), text=text)
    except Exception as exc:
        logger.warning("Failed sending logs message: %s", exc)
