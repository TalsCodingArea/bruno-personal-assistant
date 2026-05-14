from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
from typing import Any, Dict

from telegram.ext import ContextTypes

from personal_assistant.telegram.logging import safe_log

logger = logging.getLogger("telegram-assistant")
AUTOMATION_FUNCTIONS: Dict[str, Any] | None = None


def _load_automation_functions() -> Dict[str, Any]:
    import automation_functions as automation_module

    functions: Dict[str, Any] = {}
    for name in dir(automation_module):
        if name.startswith("_"):
            continue
        obj = getattr(automation_module, name)
        if not inspect.isfunction(obj) or obj.__module__ != automation_module.__name__:
            continue
        functions[name] = obj
    return functions


def _get_automation_functions() -> Dict[str, Any]:
    global AUTOMATION_FUNCTIONS
    if AUTOMATION_FUNCTIONS is None:
        AUTOMATION_FUNCTIONS = _load_automation_functions()
    return AUTOMATION_FUNCTIONS


def _strip_json_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _parse_automation_payload(text: str) -> Dict[str, Any]:
    payload_text = _strip_json_code_fence(text)
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        try:
            payload = ast.literal_eval(payload_text)
        except (SyntaxError, ValueError) as exc:
            raise ValueError("message must be a JSON object") from exc

    if not isinstance(payload, dict):
        raise ValueError("message must be a JSON object")

    tool_name = payload.get("tool")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValueError("`tool` must be a non-empty string")

    args = payload.get("args", {})
    if not isinstance(args, dict):
        raise ValueError("`args` must be an object")

    return {"tool": tool_name.strip(), "args": args}


def _automation_usage_message() -> str:
    return (
        "Send automation messages as JSON, for example:\n"
        '{"tool": "log_expense", "args": {"Description": "Coffee", "Amount": 12.5, "Date": "2026-04-29"}}'
    )


async def handle_automation_text(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (message.text or message.caption or "").strip()
    if not text:
        return

    try:
        payload = _parse_automation_payload(text)
    except ValueError as exc:
        logger.info("Ignoring invalid automation payload: %s", exc)
        await message.reply_text(f"Invalid automation payload: {exc}.\n\n{_automation_usage_message()}")
        return

    tool_name = payload["tool"]
    args = payload["args"]
    functions = _get_automation_functions()
    func = functions.get(tool_name)
    if not func:
        available = ", ".join(sorted(functions))
        logger.info("Ignoring unknown automation tool: %s", tool_name)
        await message.reply_text(f"Unknown automation tool: `{tool_name}`.\nAvailable tools: {available}")
        return

    try:
        inspect.signature(func).bind(**args)
    except TypeError as exc:
        logger.info("Invalid automation args for %s: %s", tool_name, exc)
        await message.reply_text(f"Invalid args for `{tool_name}`: {exc}")
        return

    await safe_log(context, f"[automation] Running: {tool_name}")
    try:
        if inspect.iscoroutinefunction(func):
            result = await func(**args)
        else:
            result = await asyncio.to_thread(func, **args)
        if result is None:
            result = "✅ Automation completed."
        await message.reply_text(str(result))
    except (TypeError, ValueError) as exc:
        logger.info("Automation args rejected for %s: %s", tool_name, exc)
        await message.reply_text(f"Invalid args for `{tool_name}`: {exc}")
    except Exception as exc:
        logger.exception("Automation tool failed: %s", tool_name)
        await safe_log(context, f"[automation:error] {tool_name}: {exc}")
        await message.reply_text(f"Automation `{tool_name}` failed.")
