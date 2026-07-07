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
from personal_assistant.tools.monthly_budget.notion_writer import build_and_upsert_monthly_budget_pages


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Create/update monthly Budget pages from the budget engine.")
    parser.add_argument("--month", help="Target month in YYYY-MM format. Defaults to current month.")
    parser.add_argument("--as-of", help="Current progress date in YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--lookback-months", type=int, default=6)
    parser.add_argument(
        "--include-non-predictable",
        action="store_true",
        help="Also create/update Budget pages for non-predictable categories.",
    )
    parser.add_argument("--apply", action="store_true", help="Actually write to Notion. Default is dry-run.")
    parser.add_argument("--json", action="store_true", help="Print full JSON result.")
    args = parser.parse_args()

    result = build_and_upsert_monthly_budget_pages(
        target_month=Month.parse(args.month) if args.month else None,
        as_of=_parse_date(args.as_of),
        lookback_months=args.lookback_months,
        dry_run=not args.apply,
        include_non_predictable=args.include_non_predictable,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    upsert = result["upsert"]
    mode = "DRY RUN" if upsert["dry_run"] else "APPLIED"
    print(f"{mode} monthly Budget pages for {upsert['month']}")
    print(f"Financial Summary: {upsert['financial_summary_url']}")
    print("")
    for write in upsert["writes"]:
        print(
            f"- {write['action']}: {write['sub_category']} | "
            f"Budget {write['budget']:,.0f} | Date {write['date']}"
        )


if __name__ == "__main__":
    main()
