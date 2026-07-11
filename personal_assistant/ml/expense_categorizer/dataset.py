from __future__ import annotations

import json
import logging
import os
from typing import Any

from personal_assistant.ml.expense_categorizer.models import TrainingExample
from personal_assistant.ml.expense_categorizer.storage import (
    base_training_set_path,
    feedback_training_set_path,
)

logger = logging.getLogger("personal-assistant.ml.expense-categorizer")

TAL_TAG = "Tal 👨🏻"
UNCATEGORIZED = "Uncategorized"


def fetch_labeled_expenses_from_notion(max_results: int = 10_000) -> list[TrainingExample]:
    """Pull every categorized, Tal-tagged expense from the Expenses DB.

    Only rows that already carry a real Category AND Sub Category are usable
    as training labels; everything else (including "Uncategorized") is skipped.
    """
    from personal_assistant.tools.notion_tools import (
        _raw_notion_response_to_dict,
        notion_get_database_pages,
    )

    expenses_database_id = os.getenv("EXPENSES_DATABASE_ID")
    if not expenses_database_id:
        raise ValueError("Missing EXPENSES_DATABASE_ID environment variable.")

    raw = notion_get_database_pages.invoke(
        {
            "database_id": expenses_database_id,
            "filter": {
                "and": [
                    {"property": "Tag", "multi_select": {"contains": TAL_TAG}},
                    {"property": "Category", "multi_select": {"does_not_contain": UNCATEGORIZED}},
                ]
            },
            "sorts": [{"property": "Date", "direction": "descending"}],
            "max_results": max_results,
        }
    )
    rows = _raw_notion_response_to_dict(
        ["Description", "Final", "Amount", "Category", "Sub Category", "Date"],
        raw,
    )

    examples: list[TrainingExample] = []
    skipped = 0
    for row in rows:
        example = _row_to_example(row)
        if example is None:
            skipped += 1
            continue
        examples.append(example)

    logger.info(
        "Fetched %d labeled expenses from Notion (%d rows skipped for missing labels)",
        len(examples),
        skipped,
    )
    return examples


def _row_to_example(row: dict[str, Any]) -> TrainingExample | None:
    description = (row.get("Description") or "").strip()
    categories = row.get("Category") or []
    sub_categories = row.get("Sub Category") or []
    if not description or not categories or not sub_categories:
        return None

    amount = row.get("Final") or row.get("Amount") or 0
    try:
        amount_value = float(amount)
    except (TypeError, ValueError):
        amount_value = 0.0

    return TrainingExample(
        description=description,
        amount=amount_value,
        date=str(row.get("Date") or ""),
        category=str(categories[0]),
        sub_category=str(sub_categories[0]),
        source="notion",
    )


# --- Local JSONL stores -----------------------------------------------------


def save_base_training_set(examples: list[TrainingExample]) -> None:
    path = base_training_set_path()
    with path.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(json.dumps(example.to_dict(), ensure_ascii=False) + "\n")
    logger.info("Saved %d base training examples to %s", len(examples), path)


def append_feedback_example(example: TrainingExample) -> None:
    path = feedback_training_set_path()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(example.to_dict(), ensure_ascii=False) + "\n")


def _load_jsonl(path) -> list[TrainingExample]:
    if not path.exists():
        return []
    examples: list[TrainingExample] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            examples.append(TrainingExample.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("Skipping malformed training example in %s", path)
    return examples


def load_all_training_examples() -> list[TrainingExample]:
    """Base Notion snapshot + review-loop feedback, feedback last.

    Feedback examples deliberately come last so that if the same expense
    appears in both (e.g. after a re-pull from Notion), the human-confirmed
    label is what deduplication keeps.
    """
    examples = _load_jsonl(base_training_set_path()) + _load_jsonl(feedback_training_set_path())

    deduplicated: dict[tuple[str, str, str], TrainingExample] = {}
    for example in examples:
        deduplicated[(example.description, example.date, f"{example.amount:.2f}")] = example
    return list(deduplicated.values())
