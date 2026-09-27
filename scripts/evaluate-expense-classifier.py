#!/usr/bin/env python3
"""Evaluate the history stage against a chronological JSON expense export."""

import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from financial_agent.domain.models import Transaction
from financial_agent.services.expense_classification import (
    ExpenseClassificationPolicy,
    evaluate_history_classifier,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        type=Path,
        help="JSON list of expenses with id, description, occurred_on, category, and subcategory",
    )
    parser.add_argument("--threshold", type=Decimal, default=Decimal("0.80"))
    args = parser.parse_args()

    raw = json.loads(args.path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("expense export must be a JSON list")
    transactions = tuple(_transaction(item) for item in raw)
    result = evaluate_history_classifier(
        transactions,
        policy=ExpenseClassificationPolicy(confidence_threshold=args.threshold),
    )
    print(
        json.dumps(
            {
                "eligible": result.eligible,
                "classified": result.classified,
                "correct": result.correct,
                "coverage": str(result.coverage),
                "accuracy": str(result.accuracy),
                "threshold": str(args.threshold),
            },
            indent=2,
        )
    )


def _transaction(value: Any) -> Transaction:
    if not isinstance(value, dict):
        raise ValueError("each expense must be a JSON object")
    return Transaction(
        id=str(value["id"]),
        description=str(value["description"]),
        occurred_on=date.fromisoformat(str(value["occurred_on"])),
        final_amount=Decimal(str(value.get("final_amount", "0"))),
        category=_optional_text(value.get("category")),
        subcategory=_optional_text(value.get("subcategory")),
    )


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


if __name__ == "__main__":
    main()
