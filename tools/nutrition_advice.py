from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage

from agent.llm import get_llm
from tools.nutrition_tools import DEFAULT_TIMEZONE, DEFAULT_USER_NAME, get_nutrition_snapshot

logger = logging.getLogger(__name__)


def _period_for_hour(hour: int) -> str:
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 16:
        return "midday"
    if 16 <= hour < 21:
        return "evening"
    return "late night"


def get_current_time_context(timezone: str = DEFAULT_TIMEZONE) -> Dict[str, Any]:
    now = datetime.now(ZoneInfo(timezone))
    return {
        "timezone": timezone,
        "iso": now.isoformat(timespec="minutes"),
        "hour": now.hour,
        "period": _period_for_hour(now.hour),
    }


def _safe_json_loads(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Nutrition advice LLM returned non-JSON: %s", raw)
        return {
            "needs_clarification": True,
            "clarifying_question": "What food are you planning to eat, and roughly how is it prepared?",
            "recommendation": "",
            "reasoning": "The model response could not be parsed.",
            "suggested_quantity": "",
            "estimated_macros": {},
        }
    if not isinstance(data, dict):
        return {
            "needs_clarification": True,
            "clarifying_question": "What food are you planning to eat, and roughly how is it prepared?",
            "recommendation": "",
            "reasoning": "The model response was not an object.",
            "suggested_quantity": "",
            "estimated_macros": {},
        }
    return data


def recommend_food_quantity(
    planned_food_text: str,
    *,
    user_name: str = DEFAULT_USER_NAME,
    snapshot: Optional[Dict[str, Any]] = None,
    timezone: str = DEFAULT_TIMEZONE,
    llm=None,
) -> Dict[str, Any]:
    """
    Recommend how much of a planned food the user should eat to stay near macro goals.

    The LLM estimates food macros from the user's plain-text food description and
    returns a practical quantity recommendation. No Notion writes happen here.
    """
    planned_food = (planned_food_text or "").strip()
    if not planned_food:
        return {
            "needs_clarification": True,
            "clarifying_question": "What are you planning to eat?",
            "recommendation": "",
            "reasoning": "No planned food was provided.",
            "suggested_quantity": "",
            "estimated_macros": {},
            "snapshot": snapshot,
            "time": get_current_time_context(timezone),
        }

    nutrition_snapshot = snapshot or get_nutrition_snapshot(user_name, timezone=timezone)
    time_context = get_current_time_context(timezone)
    goal = nutrition_snapshot.get("goal") or {}
    if not any(float(goal.get(field) or 0) > 0 for field in ("calories", "protein", "carbs", "fats")):
        return {
            "needs_setup": True,
            "needs_clarification": False,
            "clarifying_question": "",
            "recommendation": (
                f"I found {user_name}'s macro profile in Notion, but Calories, Protein, "
                "Carbs, and Fats are all empty or zero. Fill those goal values first, "
                "then send the food again."
            ),
            "reasoning": "Macro goals are required before quantity advice can be calculated.",
            "suggested_quantity": "",
            "estimated_macros": {},
            "confidence": "high",
            "snapshot": nutrition_snapshot,
            "time": time_context,
        }

    model = llm or get_llm()

    system = """You are Tal's practical nutrition assistant.

Return valid JSON only. Do not include markdown.

Goal:
Recommend how much of the planned food Tal should eat so the day stays inside
the macro goal as much as possible.

Rules:
- Use the remaining daily macros as hard constraints when possible.
- Consider time of day. Earlier in the day can leave room for later meals; late
  night recommendations should be more conservative.
- If exact macros are unknown, estimate from common nutrition data and say that
  it is an estimate in the reasoning.
- If the planned food is too vague to estimate a quantity, ask exactly one
  clarifying question.
- Prefer practical units: grams, pieces, cups, tablespoons, or servings.
- If no meaningful macros remain, recommend a small portion or a better
  alternative instead of pretending the planned food fits.

JSON shape:
{
  "needs_clarification": boolean,
  "clarifying_question": string,
  "recommendation": string,
  "suggested_quantity": string,
  "reasoning": string,
  "estimated_macros": {
    "calories": number,
    "protein": number,
    "carbs": number,
    "fats": number
  },
  "confidence": "low" | "medium" | "high"
}
"""

    user = {
        "planned_food": planned_food,
        "user": user_name,
        "date": nutrition_snapshot.get("date"),
        "time": time_context,
        "consumed_macros": nutrition_snapshot.get("consumed", {}),
        "macro_goal": nutrition_snapshot.get("goal", {}),
        "remaining_macros": nutrition_snapshot.get("remaining", {}),
    }

    response = model.invoke(
        [
            SystemMessage(content=system),
            HumanMessage(content=json.dumps(user, ensure_ascii=False)),
        ]
    )
    advice = _safe_json_loads(response.content or "")

    estimated = advice.get("estimated_macros")
    if not isinstance(estimated, dict):
        estimated = {}
    normalized_estimate = {}
    for field in ("calories", "protein", "carbs", "fats"):
        value = estimated.get(field, 0)
        normalized_estimate[field] = float(value or 0) if isinstance(value, (int, float)) else 0.0

    return {
        "needs_setup": False,
        "needs_clarification": bool(advice.get("needs_clarification", False)),
        "clarifying_question": str(advice.get("clarifying_question") or ""),
        "recommendation": str(advice.get("recommendation") or ""),
        "suggested_quantity": str(advice.get("suggested_quantity") or ""),
        "reasoning": str(advice.get("reasoning") or ""),
        "estimated_macros": normalized_estimate,
        "confidence": advice.get("confidence") if advice.get("confidence") in {"low", "medium", "high"} else "low",
        "snapshot": nutrition_snapshot,
        "time": time_context,
    }
