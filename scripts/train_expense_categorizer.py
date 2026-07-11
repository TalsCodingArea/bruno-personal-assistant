"""Train (or retrain) the expense categorizer from Notion.

Pulls every categorized expense with the "Tal 👨🏻" tag from the Expenses DB,
snapshots it locally, fits the model (including any review feedback collected
so far), and saves it to budget_data/ml/.

Usage:
    python scripts/train_expense_categorizer.py

Run this once for the initial training, and again whenever you want to refresh
the base dataset from Notion (day-to-day feedback retraining happens
automatically in the review loop and does not need this script).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    from personal_assistant.ml.expense_categorizer.service import train_from_notion
    from personal_assistant.ml.expense_categorizer.storage import model_path

    print("Fetching labeled Tal expenses from Notion and training...")
    try:
        metadata = train_from_notion()
    except ValueError as exc:
        print(f"Training aborted: {exc}")
        return 1

    print("\nDone.")
    print(f"  Model saved to:       {model_path()}")
    print(f"  Training examples:    {metadata['n_examples']}")
    print(f"  Sub-categories seen:  {metadata['n_sub_categories']}")
    holdout = metadata.get("holdout_accuracy")
    print(f"  Holdout accuracy:     {holdout if holdout is not None else 'n/a (dataset too small to split)'}")
    print("\nNew uncategorized expenses will now get suggestions in the review queue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
