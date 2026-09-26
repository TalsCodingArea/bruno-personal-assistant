"""Explicit trusted JSON automation channel."""

import json
from typing import Any

from telegram.ext import ContextTypes

from bruno.checkups import format_checkup_message
from bruno.config import BrunoSettings
from bruno.runtime import AutomationToolNotFoundError, BrunoRuntime
from bruno.telegram_bot.logging import safe_log


def parse_automation_payload(text: str) -> tuple[str, dict[str, Any]]:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError("message must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("message must be a JSON object")
    tool_name = payload.get("tool")
    arguments = payload.get("args", {})
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise ValueError("tool must be a non-empty string")
    if not isinstance(arguments, dict):
        raise ValueError("args must be an object")
    return tool_name.strip(), arguments


async def handle_automation_text(
    message: Any,
    context: ContextTypes.DEFAULT_TYPE,
    runtime: BrunoRuntime,
    settings: BrunoSettings,
) -> None:
    try:
        tool_name, arguments = parse_automation_payload(
            (message.text or message.caption or "").strip()
        )
        if tool_name == "check_expenses":
            schedule_expense_checkup(runtime, context, settings, fallback_chat=message.chat_id)
            await message.reply_text(_scheduled_message(settings))
            return
        result = await runtime.invoke_automation(
            tool_name, normalize_automation_arguments(tool_name, arguments)
        )
        await message.reply_text(format_automation_result(result))
        if _logged_expense(result):
            schedule_expense_checkup(
                runtime,
                context,
                settings,
                fallback_chat=message.chat_id,
            )
    except AutomationToolNotFoundError:
        await message.reply_text("No automation tool found")
    except (TypeError, ValueError) as exc:
        await message.reply_text(f"Invalid automation: {exc}")
    except Exception as exc:
        await safe_log(context, settings, f"[automation:error] {exc}")
        await message.reply_text("Automation failed. Check the logs chat.")


def schedule_expense_checkup(
    runtime: BrunoRuntime,
    context: ContextTypes.DEFAULT_TYPE,
    settings: BrunoSettings,
    *,
    fallback_chat: int | str,
) -> None:
    destination = settings.channels.personal_assistant or fallback_chat

    async def run() -> None:
        try:
            result = await runtime.invoke_automation("check_expenses", {})
            message = format_checkup_message(result)
            if message:
                await context.bot.send_message(chat_id=destination, text=message)
        except Exception as exc:
            await safe_log(context, settings, f"[expense-checkup:error] {exc}")

    runtime.debouncer.schedule("expenses", run)


def normalize_expense_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Accept Bruno's existing title-cased Notion automation payloads."""

    aliases = {
        "Description": "description",
        "Amount": "amount",
        "Date": "occurred_on",
        "Category": "category",
        "Sub Category": "subcategory",
        "Payment Method": "payment_method",
        "Type": "expense_type",
        "Tag": "tags",
        "Timezone": "timezone",
    }
    normalized = {aliases.get(key, key): value for key, value in arguments.items()}
    if "amount" in normalized:
        normalized["amount"] = str(normalized["amount"])
    for name in ("category", "subcategory", "tags"):
        value = normalized.get(name)
        if isinstance(value, str):
            normalized[name] = [value]
    return normalized


def normalize_automation_arguments(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Preserve legacy payload aliases without constraining registry dispatch."""

    if tool_name == "log_expense":
        return normalize_expense_arguments(arguments)
    if tool_name not in {"auto_expense_tool", "log_txt_expense"}:
        return dict(arguments)
    aliases = {
        "Description": "description",
        "Amount": "amount",
        "Text": "text",
        "Tag": "tags",
        "Timezone": "timezone",
    }
    normalized = {aliases.get(key, key): value for key, value in arguments.items()}
    if isinstance(normalized.get("tags"), str):
        normalized["tags"] = [normalized["tags"]]
    return normalized


def format_automation_result(result: Any) -> str:
    """Render known expense results compactly and keep future tools usable."""

    if isinstance(result, dict):
        message = result.get("message")
        if isinstance(message, str) and message:
            return message
        if _logged_expense(result):
            text = f"✅ Logged expense: {result['description']} — ₪{result['amount']}"
            if result.get("url"):
                text += f"\n{result['url']}"
            return text
        return json.dumps(result, ensure_ascii=False, default=str)
    return str(result)


def _logged_expense(result: Any) -> bool:
    return (
        isinstance(result, dict)
        and isinstance(result.get("page_id"), str)
        and bool(result["page_id"])
        and "description" in result
        and "amount" in result
    )


def _scheduled_message(settings: BrunoSettings) -> str:
    minutes = settings.expense_checkup_delay_seconds / 60
    return f"⏳ Expense check-up scheduled after {minutes:g} quiet minute(s)."
