from __future__ import annotations

from telegram.ext import ContextTypes

from personal_assistant.telegram.logging import safe_log


async def handle_nutritionist_text(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Placeholder boundary for the nutritionist channel."""
    await safe_log(context, f"[nutritionist] Received text message from chat {message.chat_id}")
    await message.reply_text(
        "Nutritionist channel is connected. The nutrition workflow is not implemented yet."
    )
