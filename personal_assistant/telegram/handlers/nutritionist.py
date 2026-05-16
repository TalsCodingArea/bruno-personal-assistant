from __future__ import annotations

import asyncio
import logging

from telegram.ext import ContextTypes

from personal_assistant.telegram.logging import safe_log

logger = logging.getLogger("telegram-assistant")


def _format_macro_line(label: str, macros: dict) -> str:
    return (
        f"{label}: "
        f"{macros.get('calories', 0):.0f} kcal | "
        f"P {macros.get('protein', 0):.0f}g | "
        f"C {macros.get('carbs', 0):.0f}g | "
        f"F {macros.get('fats', 0):.0f}g"
    )


def _format_nutrition_reply(advice: dict) -> str:
    if advice.get("needs_setup"):
        return advice.get("recommendation") or (
            "I found the macro profile in Notion, but the goal values are missing."
        )

    if advice.get("needs_clarification"):
        question = advice.get("clarifying_question") or "What are you planning to eat?"
        return question

    snapshot = advice.get("snapshot") or {}
    remaining = snapshot.get("remaining") or {}
    estimated = advice.get("estimated_macros") or {}

    parts = [
        advice.get("recommendation") or "I couldn't produce a specific recommendation.",
    ]

    if advice.get("suggested_quantity"):
        parts.append(f"Quantity: {advice['suggested_quantity']}")

    if any(estimated.values()):
        parts.append(_format_macro_line("Estimated for this portion", estimated))

    parts.append(_format_macro_line("Remaining today before this", remaining))

    if advice.get("reasoning"):
        parts.append(f"Why: {advice['reasoning']}")

    confidence = advice.get("confidence")
    if confidence:
        parts.append(f"Confidence: {confidence}")

    return "\n\n".join(parts)


async def handle_nutritionist_text(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle food quantity advice requests in the nutritionist channel."""
    planned_food = (message.text or message.caption or "").strip()
    if not planned_food:
        await message.reply_text("What are you planning to eat?")
        return

    await safe_log(context, f"[nutritionist] Advice request from chat {message.chat_id}: {planned_food}")
    status_msg = await message.reply_text("Checking today's macros...")

    try:
        from tools.nutrition_advice import recommend_food_quantity

        advice = await asyncio.to_thread(recommend_food_quantity, planned_food)
        await message.reply_text(_format_nutrition_reply(advice))
    except Exception as exc:
        logger.exception("Nutritionist handler failed")
        await safe_log(context, f"[nutritionist:error] {exc}")
        await message.reply_text("I couldn't calculate nutrition advice right now.")
    finally:
        try:
            await context.bot.delete_message(chat_id=message.chat_id, message_id=status_msg.message_id)
        except Exception:
            pass
