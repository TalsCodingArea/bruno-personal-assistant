from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from personal_assistant.config import settings
from personal_assistant.telegram.handlers.automations import handle_automation_text
from personal_assistant.telegram.handlers.jobs import handle_jobs_channel_text
from personal_assistant.telegram.handlers.nutritionist import handle_nutritionist_text
from personal_assistant.telegram.handlers.personal import handle_personal_assistant_text
from personal_assistant.telegram.handlers.receipts import handle_receipt_pdf
from personal_assistant.telegram.logging import safe_log


def _same_chat(chat_id: int, configured_id: str) -> bool:
    return bool(configured_id) and str(chat_id) == str(configured_id)


async def route_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message or update.channel_post
    if not message:
        return

    channels = settings.channels

    if _same_chat(message.chat_id, channels.automations):
        await handle_automation_text(message, context)
        return

    if _same_chat(message.chat_id, channels.jobs):
        await handle_jobs_channel_text(message, context)
        return

    if _same_chat(message.chat_id, channels.nutritionist):
        await handle_nutritionist_text(message, context)
        return

    if _same_chat(message.chat_id, channels.receipts):
        await message.reply_text("Please send receipt PDFs as documents, not as text.")
        return

    if _same_chat(message.chat_id, channels.logs):
        await message.reply_text("This chat is for logs only. Please use the personal assistant chat for interactions.")
        return

    if _same_chat(message.chat_id, channels.personal_assistant):
        await handle_personal_assistant_text(update, context)


async def route_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if "channel_post" in update._get_attrs():
        message = update.channel_post
    if not message or not message.document:
        return

    if _same_chat(message.chat_id, settings.channels.receipts):
        await handle_receipt_pdf(update, context)
        return

    await safe_log(context, f"Received document from unregistered chat id: {message.chat_id}")
    await message.reply_text("Document uploads are only handled in the receipts chat.")
