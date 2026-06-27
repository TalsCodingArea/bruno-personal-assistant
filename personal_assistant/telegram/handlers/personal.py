from __future__ import annotations

import asyncio
import logging
import time
import random

from langchain_core.messages import AIMessage
from telegram import Message
from telegram import Update
from telegram.ext import ContextTypes

from agent.workflow import AgentEvent, stream_agent_events
from personal_assistant import runtime
from personal_assistant.telegram.formatting import markdown_v2_safe
from personal_assistant.telegram.handlers.jobs import handle_job_application
from personal_assistant.telegram.logging import safe_log
from router.intent_router import extract_url_from_message, is_cancel_intent, is_job_url_fast

logger = logging.getLogger("telegram-assistant")


_TOOL_STATUS: dict[str, str] = {
    "get_expenses_between_dates": "🔍 Fetching expenses...",
    "get_income_between_dates": "🔍 Fetching income...",
    "get_last_expenses": "🔍 Fetching recent expenses...",
    "get_finance_rules": "📋 Loading finance rules...",
    "get_database_schema": "📋 Loading database schema...",
    "get_movies_data_from_notion_database": "🎬 Fetching movies...",
    "create_idea_in_notion": "💡 Saving idea to Notion...",
    "get_exchange_rates": "💱 Fetching exchange rates...",
    "get_tase_stock_quote": "📈 Fetching stock quote...",
    "get_tase_index": "📊 Fetching market index...",
    "web_search": "🌐 Searching the web...",
}
_DEFAULT_TOOL_STATUS = "⚙️ Working on it..."
_RESPONSE_EDIT_INTERVAL_SECONDS = 0.7
_RESPONSE_EDIT_MIN_CHARS = 32

_INITIAL_PROCESSING_STATUSES = [
                            "⏳ Working on it...",
                            "🔍 Thinking...",
                            "💭 Processing...",
                            "🧠 Crunching numbers...",
                            "🔎 Investigating...",
                            "📝 Reviewing information...",
                            "🔬 Examining details...",
                            "🛠️ Working on it...",
                            "⏱️ Processing request...",
                            "📈 Evaluating options...",
                            "🧩 Piecing things together...",
                            "🗂️ Organizing information...",
                            "🖋️ Drafting response...",
                            "🧪 Experimenting...",
                            "🧭 Navigating through data...",
                            "🧵 Unraveling details...",
                            "🗃️ Sorting through information..."
                        ]
_POST_TOOL_PROCESSING_STATUSES = [
                            "⚙️ Analysing results...",
                            "🔍 Reviewing tool output...",
                            "🧠 Processing tool results...",
                            "📝 Summarizing findings...",
                            "📊 Evaluating tool data...",
                            "🧩 Piecing together insights...",
                            "🗂️ Organizing tool information...",
                            "🖋️ Drafting response based on tool output...",
                            "🧪 Experimenting with tool results...",
                            "🧭 Navigating through data...",
                            "🧵 Unraveling tool details...",
                            "🗃️ Sorting through tool information..."
                        ]


async def _keep_typing(bot, chat_id: int, stop_event: asyncio.Event) -> None:
    """Send 'typing' action every 4 seconds until stop_event is set."""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        await asyncio.sleep(4)


def _telegram_status_text(event: AgentEvent) -> str | None:
    if event.type == "processing":
        return random.choice(_POST_TOOL_PROCESSING_STATUSES) if event.message == "Processing tool result" else random.choice(_INITIAL_PROCESSING_STATUSES)
    if event.type == "tool_calling":
        return _TOOL_STATUS.get(event.tool_name or "", _DEFAULT_TOOL_STATUS)
    if event.type == "generating_response":
        return "✍️ Generating response..."
    if event.type == "done":
        return "✅ Done"
    return None


async def _edit_status(bot, chat_id: int, message_id: int, text: str, current_text: str) -> str:
    if text == current_text:
        return current_text
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
        return text
    except Exception:
        return current_text


async def _edit_response_text(
    bot,
    *,
    chat_id: int,
    message_id: int,
    text: str,
    parse_mode: str | None = None,
) -> bool:
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
        )
        return True
    except Exception:
        return False


async def _finalize_response_message(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    streamed_message: Message | None,
    response: str,
) -> None:
    formatted = markdown_v2_safe(response, preserve_formatting=True)
    if streamed_message:
        updated = await _edit_response_text(
            context.bot,
            chat_id=message.chat_id,
            message_id=streamed_message.message_id,
            text=formatted,
            parse_mode="MarkdownV2",
        )
        if updated:
            return
    await message.reply_text(formatted, parse_mode="MarkdownV2")


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
    out = {}
    current_status = "⏳ Working on it..."
    streamed_message: Message | None = None
    streamed_text = ""
    last_streamed_text = ""
    last_stream_edit_at = 0.0
    try:
        async for event in stream_agent_events(agent, session_id=chat_id, user_text=user_text):
            status_text = _telegram_status_text(event)
            if status_text:
                current_status = await _edit_status(
                    context.bot,
                    message.chat_id,
                    status_msg.message_id,
                    status_text,
                    current_status,
                )
            if event.type == "response_delta" and event.content_delta:
                streamed_text += event.content_delta
                now = time.monotonic()
                should_edit = (
                    streamed_message is None
                    or len(streamed_text) - len(last_streamed_text) >= _RESPONSE_EDIT_MIN_CHARS
                    or now - last_stream_edit_at >= _RESPONSE_EDIT_INTERVAL_SECONDS
                )
                if should_edit:
                    if streamed_message is None:
                        streamed_message = await message.reply_text(streamed_text)
                    else:
                        await _edit_response_text(
                            context.bot,
                            chat_id=message.chat_id,
                            message_id=streamed_message.message_id,
                            text=streamed_text,
                        )
                    last_streamed_text = streamed_text
                    last_stream_edit_at = now
            if event.type == "done":
                out = event.output or {}
            elif event.type == "error":
                logger.error("Agent error for chat %s: %s", chat_id, event.error)
                await message.reply_text("Something went wrong — try again.")
                await safe_log(context, f"[agent:error] {event.error}")
                return
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
            await _finalize_response_message(message, context, streamed_message, response)
        await handle_job_application(url, message, context)
        return

    if response:
        await _finalize_response_message(message, context, streamed_message, response)
    else:
        await message.reply_text("Sorry, I couldn't generate a response for that.")
