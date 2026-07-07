from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from personal_assistant.tools.monthly_budget.models import Month
from personal_assistant.tools.monthly_budget.notion_preview import (
    build_monthly_budget_preview,
    format_monthly_budget_preview,
)


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a read-only Notion-backed monthly budget preview.")
    parser.add_argument("--month", help="Target month in YYYY-MM format. Defaults to current month.")
    parser.add_argument("--as-of", help="Current progress date in YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--lookback-months", type=int, default=6)
    parser.add_argument("--savings-rate", type=float, default=0.0)
    parser.add_argument("--safety-buffer-rate", type=float, default=0.0)
    parser.add_argument("--fallback-income", type=float)
    parser.add_argument("--json", action="store_true", help="Print full JSON instead of a summary.")
    args = parser.parse_args()

    preview = build_monthly_budget_preview(
        target_month=Month.parse(args.month) if args.month else None,
        as_of=_parse_date(args.as_of),
        lookback_months=args.lookback_months,
        savings_rate=args.savings_rate,
        safety_buffer_rate=args.safety_buffer_rate,
        fallback_income=args.fallback_income,
    )

    if args.json:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
    else:
        print(format_monthly_budget_preview(preview))


if __name__ == "__main__":
    main()
