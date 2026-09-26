"""Deterministic Telegram channel routing."""

from telegram import Update
from telegram.ext import ContextTypes

from bruno.config import BrunoSettings
from bruno.runtime import BrunoRuntime
from bruno.telegram_bot.automations import handle_automation_text
from bruno.telegram_bot.logging import safe_log
from bruno.telegram_bot.personal import handle_personal_text
from bruno.telegram_bot.receipts import handle_receipt_pdf


def _same_chat(chat_id: int, configured: str) -> bool:
    return bool(configured) and str(chat_id) == configured


def _dependencies(
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[BrunoRuntime, BrunoSettings]:
    runtime = context.application.bot_data["runtime"]
    settings = context.application.bot_data["settings"]
    if not isinstance(runtime, BrunoRuntime) or not isinstance(settings, BrunoSettings):
        raise RuntimeError("Bruno application dependencies are not configured")
    return runtime, settings


async def route_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message or update.channel_post
    if message is None:
        return
    runtime, settings = _dependencies(context)
    channels = settings.channels
    if _same_chat(message.chat_id, channels.automations):
        await handle_automation_text(message, context, runtime, settings)
    elif _same_chat(message.chat_id, channels.receipts):
        await message.reply_text("Please send receipt PDFs as documents.")
    elif _same_chat(message.chat_id, channels.logs):
        await message.reply_text("This chat is for logs only.")
    elif _same_chat(message.chat_id, channels.personal_assistant):
        await handle_personal_text(update, context, runtime, settings)


async def route_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message or update.channel_post
    if message is None or message.document is None:
        return
    runtime, settings = _dependencies(context)
    if _same_chat(message.chat_id, settings.channels.receipts):
        await handle_receipt_pdf(update, context, runtime, settings)
        return
    await safe_log(
        context,
        settings,
        f"Document received from unregistered chat {message.chat_id}",
    )
    await message.reply_text("Documents are only handled in the receipts chat.")
