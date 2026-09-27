"""Personal-chat finance capability handler with Bruno's streaming UX."""

import asyncio
import json
import time
from contextlib import suppress
from typing import Any

from telegram import Message, Update
from telegram.ext import ContextTypes

from bruno.config import BrunoSettings
from bruno.runtime import BrunoRuntime
from bruno.telegram_bot.formatting import markdown_v2_safe
from bruno.telegram_bot.logging import safe_log
from bruno.workflow import AgentEvent, stream_agent_events

_EDIT_INTERVAL_SECONDS = 0.7
_EDIT_MIN_CHARS = 32


async def handle_personal_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    runtime: BrunoRuntime,
    settings: BrunoSettings,
) -> None:
    message = update.message or update.channel_post
    if message is None:
        return
    user_text = (message.text or message.caption or "").strip()
    if not user_text:
        return
    status = await message.reply_text("⏳ Working on it...")
    stop_typing = asyncio.Event()
    typing = asyncio.create_task(
        _keep_typing(context, message.chat_id, stop_typing)
    )
    streamed_message: Message | None = None
    streamed_text = ""
    last_edit_text = ""
    last_edit_at = 0.0
    output: dict[str, Any] = {}

    try:
        capability = await runtime.select_capability(str(message.chat_id), user_text)
    except Exception as exc:
        stop_typing.set()
        await typing
        await status.delete()
        await safe_log(context, settings, f"[router:error] {exc}")
        await message.reply_text("I couldn't route that request. Please try again.")
        return
    if capability != "finance":
        stop_typing.set()
        await typing
        await status.delete()
        await message.reply_text(
            "That belongs to the general capability, which has not been added "
            "to the new Bruno shell yet."
        )
        return

    async def run(callbacks: list[Any]) -> dict[str, Any]:
        return await runtime.finance_turn(str(message.chat_id), user_text, callbacks)

    try:
        async for event in stream_agent_events(run):
            await _update_status(context, status, event)
            if event.type == "response_delta" and event.content_delta:
                streamed_text += event.content_delta
                now = time.monotonic()
                if (
                    streamed_message is None
                    or len(streamed_text) - len(last_edit_text) >= _EDIT_MIN_CHARS
                    or now - last_edit_at >= _EDIT_INTERVAL_SECONDS
                ):
                    if streamed_message is None:
                        streamed_message = await message.reply_text(streamed_text)
                    else:
                        await _edit(
                            context,
                            message.chat_id,
                            streamed_message.message_id,
                            streamed_text,
                        )
                    last_edit_text = streamed_text
                    last_edit_at = now
            if event.type in {"done", "approval"}:
                output = event.output or {}
            elif event.type == "error":
                raise RuntimeError(event.error or "Finance capability failed")
    except Exception as exc:
        await safe_log(context, settings, f"[finance:error] {exc}")
        await message.reply_text("Something went wrong. Please try again.")
        return
    finally:
        stop_typing.set()
        await typing
        with suppress(Exception):
            await status.delete()

    approval = output.get("approval_request")
    notices = output.get("deferred_notices", ())
    if isinstance(notices, (list, tuple)):
        for notice in notices:
            await message.reply_text(str(notice))
    if approval is not None:
        request_text = json.dumps(approval, ensure_ascii=False, indent=2)
        await message.reply_text(
            "Approval required:\n"
            f"{request_text}\n\nReply `approve` or `reject`.",
        )
        return
    response = str(output.get("output") or "")
    if response:
        formatted = markdown_v2_safe(response, preserve_formatting=True)
        if streamed_message is not None and await _edit(
            context,
            message.chat_id,
            streamed_message.message_id,
            formatted,
            parse_mode="MarkdownV2",
        ):
            return
        await message.reply_text(formatted, parse_mode="MarkdownV2")
    else:
        await message.reply_text("I couldn't generate a response.")


async def _keep_typing(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        with suppress(Exception):
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        try:
            await asyncio.wait_for(stop.wait(), timeout=4)
        except TimeoutError:
            continue


async def _update_status(
    context: ContextTypes.DEFAULT_TYPE, status: Message, event: AgentEvent
) -> None:
    text = {
        "processing": "⚙️ Processing...",
        "tool_calling": f"🔧 Running {event.tool_name or 'tool'}...",
        "generating_response": "✍️ Generating response...",
    }.get(event.type)
    if text:
        await _edit(context, status.chat_id, status.message_id, text)


async def _edit(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    text: str,
    *,
    parse_mode: str | None = None,
) -> bool:
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
        )
        return True
    except Exception:
        return False
