from __future__ import annotations

import os
from pathlib import Path

# All expense-categorizer state lives under budget_data/ml/ next to the other
# on-device assistant state (financial advisor profile, recommendations, ...).
# ML_DATA_DIR overrides the location, which the tests use to stay isolated.

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "budget_data" / "ml"


def data_dir() -> Path:
    override = os.getenv("ML_DATA_DIR")
    directory = Path(override) if override else _DEFAULT_DATA_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def model_path() -> Path:
    return data_dir() / "expense_categorizer.joblib"


def model_metadata_path() -> Path:
    return data_dir() / "expense_categorizer_metadata.json"


def base_training_set_path() -> Path:
    """Snapshot of the labeled expenses pulled from Notion at training time."""
    return data_dir() / "training_base.jsonl"


def feedback_training_set_path() -> Path:
    """Confirmed / corrected examples appended by the review loop."""
    return data_dir() / "training_feedback.jsonl"


def review_queue_path() -> Path:
    return data_dir() / "expense_review_queue.json"
