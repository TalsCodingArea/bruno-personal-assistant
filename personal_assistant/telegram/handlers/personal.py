from __future__ import annotations

import asyncio
import logging

from langchain_core.messages import AIMessage
from telegram import Update
from telegram.ext import ContextTypes

from personal_assistant import runtime
from personal_assistant.telegram.handlers.jobs import handle_job_application
from personal_assistant.telegram.logging import safe_log
from router.intent_router import extract_url_from_message, is_cancel_intent, is_job_url_fast
from tools.telegram_tools import TelegramStatusCallback, markdown_v2_safe

logger = logging.getLogger("telegram-assistant")


async def _keep_typing(bot, chat_id: int, stop_event: asyncio.Event) -> None:
    """Send 'typing' action every 4 seconds until stop_event is set."""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        await asyncio.sleep(4)


async def _handle_budget_review(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    start: bool = False,
) -> None:
    """Drive the LangGraph budget review workflow for one Telegram turn."""
    chat_id = str(message.chat_id)
    try:
        if start:
            import time

            thread_id = f"budget_review_{chat_id}_{int(time.time())}"
            runtime.budget_review_sessions[chat_id] = thread_id
            config = {"configurable": {"thread_id": thread_id}}
            state = await runtime.async_start_budget_review(runtime.budget_review_graph, config)
        else:
            thread_id = runtime.budget_review_sessions[chat_id]
            config = {"configurable": {"thread_id": thread_id}}
            user_text = (message.text or "").strip()
            state = await runtime.async_continue_budget_review(runtime.budget_review_graph, config, user_text)

        msgs = state.get("messages", [])
        last_ai = next((m for m in reversed(msgs) if isinstance(m, AIMessage)), None)
        if last_ai:
            await message.reply_text(last_ai.content)

        if state.get("phase") == "done":
            runtime.budget_review_sessions.pop(chat_id, None)

    except Exception as exc:
        logger.exception("Budget review workflow error for chat %s", chat_id)
        runtime.budget_review_sessions.pop(chat_id, None)
        await message.reply_text("Something went wrong with the budget review. Please try again.")
        await safe_log(context, f"[budget_review:error] {exc}")


async def _handle_budget_workflow(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    start: bool = False,
) -> None:
    """Drive the LangGraph budget workflow for one Telegram turn."""
    chat_id = str(message.chat_id)

    try:
        if start:
            import time

            thread_id = f"budget_{chat_id}_{int(time.time())}"
            runtime.budget_sessions[chat_id] = thread_id
            config = {"configurable": {"thread_id": thread_id}}
            state = await runtime.async_start_budget_workflow(runtime.budget_graph, config)
        else:
            thread_id = runtime.budget_sessions[chat_id]
            config = {"configurable": {"thread_id": thread_id}}
            user_text = (message.text or "").strip()
            state = await runtime.async_continue_budget_workflow(runtime.budget_graph, config, user_text)

        msgs = state.get("messages", [])
        last_ai = next((m for m in reversed(msgs) if isinstance(m, AIMessage)), None)
        if last_ai:
            await message.reply_text(last_ai.content)

        if state.get("phase") == "done":
            runtime.budget_sessions.pop(chat_id, None)
            await safe_log(context, f"[budget] Workflow complete for chat {chat_id}")

    except Exception as exc:
        logger.exception("Budget workflow error for chat %s", chat_id)
        runtime.budget_sessions.pop(chat_id, None)
        await message.reply_text("Something went wrong with the budget workflow. Please try again.")
        await safe_log(context, f"[budget:error] {exc}")


async def handle_personal_assistant_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return
    user_text = (message.text or message.caption or "").strip()
    if not user_text:
        return

    chat_id = str(message.chat_id)

    if chat_id in runtime.budget_review_sessions:
        if is_cancel_intent(user_text):
            runtime.budget_review_sessions.pop(chat_id, None)
            await message.reply_text("Budget review cancelled.")
            return
        await _handle_budget_review(message, context, start=False)
        return

    if chat_id in runtime.budget_sessions:
        if is_cancel_intent(user_text):
            runtime.budget_sessions.pop(chat_id, None)
            await message.reply_text("Workflow cancelled. What else can I do for you?")
            return
        if is_job_url_fast(user_text):
            runtime.budget_sessions.pop(chat_id, None)
            url = extract_url_from_message(user_text)
            await handle_job_application(url, message, context)
            return
        await _handle_budget_workflow(message, context, start=False)
        return

    agent = runtime.get_or_build_agent(chat_id)
    status_msg = await message.reply_text("⏳ Working on it...")
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(context.bot, message.chat_id, stop_typing))
    try:
        callback = TelegramStatusCallback(context.bot, message.chat_id, status_msg.message_id)
        out = await agent.ainvoke(
            {"input": user_text},
            config={
                "configurable": {"session_id": chat_id},
                "callbacks": [callback],
            },
        )
    except Exception as exc:
        logger.exception("Agent error for chat %s", chat_id)
        await message.reply_text("Something went wrong — try again.")
        await safe_log(context, f"[agent:error] {exc}")
        return
    finally:
        stop_typing.set()
        await typing_task
        try:
            await context.bot.delete_message(chat_id=message.chat_id, message_id=status_msg.message_id)
        except Exception:
            pass

    response = out.get("output", "")

    if chat_id in runtime.pending_jobs:
        url = runtime.pending_jobs.pop(chat_id)
        if response:
            await message.reply_text(
                markdown_v2_safe(response, preserve_formatting=True),
                parse_mode="MarkdownV2",
            )
        await handle_job_application(url, message, context)
        return

    if response:
        await message.reply_text(markdown_v2_safe(response, preserve_formatting=True), parse_mode="MarkdownV2")
    else:
        await message.reply_text("Sorry, I couldn't generate a response for that.")
