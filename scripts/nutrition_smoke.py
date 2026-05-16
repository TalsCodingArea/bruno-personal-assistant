from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.nutrition_advice import recommend_food_quantity


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a live nutrition advice smoke test.")
    parser.add_argument("food", nargs="+", help="Food Tal is planning to eat.")
    parser.add_argument("--user", default="Tal", help="User name in the macros database.")
    args = parser.parse_args()

    advice = recommend_food_quantity(" ".join(args.food), user_name=args.user)
    output = {
        "needs_setup": advice.get("needs_setup", False),
        "needs_clarification": advice["needs_clarification"],
        "clarifying_question": advice["clarifying_question"],
        "recommendation": advice["recommendation"],
        "suggested_quantity": advice["suggested_quantity"],
        "reasoning": advice["reasoning"],
        "estimated_macros": advice["estimated_macros"],
        "confidence": advice["confidence"],
        "snapshot": {
            "date": advice["snapshot"]["date"],
            "consumed": advice["snapshot"]["consumed"],
            "goal": advice["snapshot"]["goal"],
            "remaining": advice["snapshot"]["remaining"],
        },
        "time": advice["time"],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
