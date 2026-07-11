from __future__ import annotations

import json
import logging
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import FeatureUnion, Pipeline

from personal_assistant.ml.expense_categorizer.models import CategoryPrediction, TrainingExample
from personal_assistant.ml.expense_categorizer.storage import model_metadata_path, model_path

logger = logging.getLogger("personal-assistant.ml.expense-categorizer")

# Below this many labeled examples, training is refused: a model fitted on a
# handful of rows would confidently mislabel everything.
MIN_TRAINING_EXAMPLES = 30


def _amount_bucket(amount: float) -> str:
    """Coarse log-scale bucket so the amount can participate as a text token.

    Folding numeric features into the text keeps the pipeline a single
    TF-IDF -> classifier chain (no ColumnTransformer/pandas), which is plenty
    at this dataset size and much easier to reason about.
    """
    if amount <= 0:
        return "amt_zero"
    return f"amt_e{int(math.log10(amount))}"


def _weekday_token(date_text: str) -> str:
    try:
        return f"wd_{datetime.fromisoformat(date_text.replace('Z', '+00:00')).strftime('%a').lower()}"
    except ValueError:
        return "wd_unknown"


def featurize(description: str, amount: float, date: str) -> str:
    return f"{description} {_amount_bucket(amount)} {_weekday_token(date)}"


def _build_sklearn_pipeline() -> Pipeline:
    return Pipeline(
        [
            (
                "features",
                FeatureUnion(
                    [
                        ("words", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True)),
                        # Character n-grams are what make this work for Hebrew
                        # merchant names, typos, and truncated SMS text.
                        ("chars", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)),
                    ]
                ),
            ),
            ("classifier", LogisticRegression(max_iter=2000, class_weight="balanced")),
        ]
    )


class ExpenseCategorizerModel:
    """Sub-category classifier + learned sub-category -> category mapping.

    Sub Category is the finer label, so that's what the classifier predicts;
    Category is derived from the mapping observed in training data (each
    sub-category belongs to one category in Tal's budgeting system, so the
    mapping is essentially deterministic).
    """

    def __init__(self, pipeline: Pipeline, sub_to_category: dict[str, str]) -> None:
        self._pipeline = pipeline
        self._sub_to_category = sub_to_category

    # --- training -----------------------------------------------------------

    @classmethod
    def train(cls, examples: list[TrainingExample]) -> tuple["ExpenseCategorizerModel", dict]:
        """Fit on all examples; returns (model, metadata with holdout metrics)."""
        if len(examples) < MIN_TRAINING_EXAMPLES:
            raise ValueError(
                f"Need at least {MIN_TRAINING_EXAMPLES} labeled expenses to train, got {len(examples)}."
            )

        texts = [featurize(e.description, e.amount, e.date) for e in examples]
        labels = [e.sub_category for e in examples]

        holdout_accuracy = cls._holdout_accuracy(texts, labels)

        pipeline = _build_sklearn_pipeline()
        pipeline.fit(texts, labels)

        sub_to_category = cls._learn_sub_to_category(examples)

        metadata = {
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "n_examples": len(examples),
            "n_sub_categories": len(set(labels)),
            "holdout_accuracy": holdout_accuracy,
        }
        logger.info("Trained expense categorizer: %s", metadata)
        return cls(pipeline, sub_to_category), metadata

    @staticmethod
    def _holdout_accuracy(texts: list[str], labels: list[str]) -> float | None:
        """Accuracy on a 20% split, as an honest signal of model quality.

        The returned model is still trained on ALL data afterwards; this split
        exists only for the metric. Falls back to None when classes are too
        small to stratify.
        """
        try:
            x_train, x_test, y_train, y_test = train_test_split(
                texts, labels, test_size=0.2, random_state=42, stratify=labels
            )
        except ValueError:
            try:
                x_train, x_test, y_train, y_test = train_test_split(
                    texts, labels, test_size=0.2, random_state=42
                )
            except ValueError:
                return None
        evaluation_pipeline = _build_sklearn_pipeline()
        try:
            evaluation_pipeline.fit(x_train, y_train)
            return round(float(evaluation_pipeline.score(x_test, y_test)), 4)
        except ValueError:
            return None

    @staticmethod
    def _learn_sub_to_category(examples: list[TrainingExample]) -> dict[str, str]:
        votes: dict[str, Counter] = defaultdict(Counter)
        for example in examples:
            votes[example.sub_category][example.category] += 1
        return {sub: counter.most_common(1)[0][0] for sub, counter in votes.items()}

    # --- inference ------------------------------------------------------------

    def predict(self, description: str, amount: float, date: str) -> CategoryPrediction:
        text = featurize(description, amount, date)
        probabilities = self._pipeline.predict_proba([text])[0]
        best_index = probabilities.argmax()
        sub_category = str(self._pipeline.classes_[best_index])
        return CategoryPrediction(
            category=self._sub_to_category.get(sub_category, "Uncategorized"),
            sub_category=sub_category,
            confidence=round(float(probabilities[best_index]), 4),
        )

    # --- persistence ----------------------------------------------------------

    def save(self, metadata: dict | None = None) -> None:
        joblib.dump(
            {"pipeline": self._pipeline, "sub_to_category": self._sub_to_category},
            model_path(),
        )
        if metadata is not None:
            model_metadata_path().write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    @classmethod
    def load(cls) -> "ExpenseCategorizerModel | None":
        path = model_path()
        if not path.exists():
            return None
        try:
            payload = joblib.load(path)
            return cls(payload["pipeline"], payload["sub_to_category"])
        except Exception:
            logger.exception("Failed to load expense categorizer model from %s", path)
            return None
