from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class TrainingExample:
    """One labeled expense the model can learn from."""

    description: str
    amount: float
    date: str  # ISO date or datetime string
    category: str
    sub_category: str
    source: str = "notion"  # "notion" (initial pull) or "feedback" (review loop)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrainingExample":
        return cls(
            description=str(data.get("description", "")),
            amount=float(data.get("amount") or 0.0),
            date=str(data.get("date", "")),
            category=str(data.get("category", "")),
            sub_category=str(data.get("sub_category", "")),
            source=str(data.get("source", "notion")),
        )


@dataclass(frozen=True)
class CategoryPrediction:
    """Model output for one expense."""

    category: str
    sub_category: str
    confidence: float  # probability of the predicted sub-category, 0..1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewItem:
    """A classified expense waiting for (or resolved by) human review."""

    review_id: str
    notion_page_id: str
    description: str
    amount: float
    date: str
    predicted_category: str | None
    predicted_sub_category: str | None
    confidence: float | None
    status: str = "pending"  # pending | confirmed | corrected | dismissed
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    notified_at: str | None = None
    resolved_at: str | None = None
    final_category: str | None = None
    final_sub_category: str | None = None

    @classmethod
    def new(
        cls,
        *,
        notion_page_id: str,
        description: str,
        amount: float,
        date: str,
        prediction: CategoryPrediction | None,
    ) -> "ReviewItem":
        return cls(
            review_id=uuid4().hex[:12],
            notion_page_id=notion_page_id,
            description=description,
            amount=amount,
            date=date,
            predicted_category=prediction.category if prediction else None,
            predicted_sub_category=prediction.sub_category if prediction else None,
            confidence=prediction.confidence if prediction else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewItem":
        return cls(**{key: data.get(key) for key in cls.__dataclass_fields__})
