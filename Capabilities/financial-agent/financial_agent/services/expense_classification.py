"""Tiered classification for newly created expenses."""

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from rapidfuzz import fuzz, process

from financial_agent.domain.models import Transaction
from financial_agent.services.category_memory import CategoryMemory

_EMPTY_LABELS = {"", "uncategorized", "unassigned", "unknown"}
_NOISE_TOKENS = {
    "cal",
    "card",
    "credit",
    "עסקה",
    "בכרטיס",
    "כרטיס",
}
_CONFIDENCE_QUANTUM = Decimal("0.01")


class ClassificationMode(StrEnum):
    """Whether classification is disabled, observed, or persisted."""

    OFF = "off"
    SHADOW = "shadow"
    APPLY = "apply"


class ClassificationStage(StrEnum):
    """The evidence stage that produced the final outcome."""

    CORRECTION = "correction"
    HISTORY = "history"
    MERCHANT = "merchant"
    WEB = "web"
    UNRESOLVED = "unresolved"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class CategoryChoice:
    """One existing category pair the model is allowed to choose."""

    category: str
    subcategory: str
    examples: tuple[str, ...]
    history_count: int


@dataclass(frozen=True, slots=True)
class SearchEvidence:
    """Bounded public context about a merchant."""

    title: str
    url: str
    content: str


@dataclass(frozen=True, slots=True)
class CategoryDecision:
    """A category choice returned by a model adapter."""

    category: str
    subcategory: str
    confidence: Decimal
    reason: str

    def __post_init__(self) -> None:
        confidence = Decimal(str(self.confidence)).quantize(_CONFIDENCE_QUANTUM)
        if not Decimal("0") <= confidence <= Decimal("1"):
            raise ValueError("classification confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence)


@dataclass(frozen=True, slots=True)
class ClassificationOutcome:
    """Observable result of classifying one expense page."""

    page_id: str
    stage: ClassificationStage
    category: str | None
    subcategory: str | None
    confidence: Decimal
    reason: str
    applied: bool
    would_apply: bool


@dataclass(frozen=True, slots=True)
class HistoryClassifierEvaluation:
    """Walk-forward quality metrics for the deterministic history stage."""

    eligible: int
    classified: int
    correct: int
    coverage: Decimal
    accuracy: Decimal


@dataclass(frozen=True, slots=True)
class ExpenseClassificationPolicy:
    """Small set of controls for the classification ladder."""

    mode: ClassificationMode = ClassificationMode.SHADOW
    confidence_threshold: Decimal = Decimal("0.80")
    history_days: int = 730
    fuzzy_score_cutoff: int = 72
    fuzzy_match_limit: int = 5

    def __post_init__(self) -> None:
        threshold = Decimal(str(self.confidence_threshold)).quantize(
            _CONFIDENCE_QUANTUM
        )
        if not Decimal("0") < threshold <= Decimal("1"):
            raise ValueError("classification threshold must be greater than 0 and at most 1")
        if self.history_days < 1:
            raise ValueError("classification history_days must be positive")
        if not 0 <= self.fuzzy_score_cutoff <= 100:
            raise ValueError("fuzzy_score_cutoff must be between 0 and 100")
        if self.fuzzy_match_limit < 1:
            raise ValueError("fuzzy_match_limit must be positive")
        object.__setattr__(self, "confidence_threshold", threshold)


class ExpenseClassificationReader(Protocol):
    async def transaction(self, page_id: str) -> Transaction | None: ...

    async def transactions(
        self, start_date: date, end_date: date, *, limit: int | None = None
    ) -> tuple[Transaction, ...]: ...


class ExpenseClassificationWriter(Protocol):
    async def apply_classification(
        self,
        page_id: str,
        category: str,
        subcategory: str,
    ) -> None: ...


class CategoryDecider(Protocol):
    async def decide(
        self,
        merchant: str,
        choices: tuple[CategoryChoice, ...],
        *,
        web_context: tuple[SearchEvidence, ...] = (),
    ) -> CategoryDecision | None: ...


class MerchantSearch(Protocol):
    async def search(self, merchant: str) -> tuple[SearchEvidence, ...]: ...


class ExpenseClassificationService:
    """Classify one expense through history, merchant inference, and optional web context."""

    def __init__(
        self,
        reader: ExpenseClassificationReader,
        writer: ExpenseClassificationWriter,
        *,
        decider: CategoryDecider | None,
        search: MerchantSearch | None,
        policy: ExpenseClassificationPolicy | None = None,
        memory: CategoryMemory | None = None,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.decider = decider
        self.search = search
        self.policy = policy or ExpenseClassificationPolicy()
        self.memory = memory

    async def classify_created_expense(
        self,
        page_id: str,
        *,
        as_of: date,
    ) -> ClassificationOutcome:
        """Classify and optionally persist one newly created expense."""

        if self.policy.mode is ClassificationMode.OFF:
            return _empty_outcome(page_id, "Expense classification is disabled.")
        current = await self.reader.transaction(page_id)
        if current is None:
            return _empty_outcome(page_id, "Expense page was not found.")
        if _has_multiple_labels(current):
            return _empty_outcome(
                page_id,
                "Existing multiple category values require manual review.",
            )
        if self.memory is not None:
            try:
                pair = await self.memory.lookup(current.description)
            except Exception:
                return _unresolved(page_id, "Merchant correction memory is unavailable or invalid.")
            if pair is not None:
                category, subcategory = pair
                return await self._finalize(
                    page_id,
                    ClassificationStage.CORRECTION,
                    CategoryDecision(
                        category, subcategory, Decimal("1"), "Explicit user merchant correction."
                    ),
                )

        if not _needs_classification(current):
            return _empty_outcome(page_id, "Expense is already categorized.")

        start = as_of - timedelta(days=self.policy.history_days)
        rows = await self.reader.transactions(start, as_of)
        history = tuple(
            item
            for item in rows
            if item.id != current.id and _usable_history_row(item, current)
        )
        choices = _category_choices(history)
        if not choices:
            return _unresolved(
                page_id,
                "No categorized history exists to define allowed category pairs.",
            )

        history_decision = _history_decision(
            current.description,
            history,
            score_cutoff=self.policy.fuzzy_score_cutoff,
            match_limit=self.policy.fuzzy_match_limit,
        )
        if self._accepted(history_decision):
            return await self._finalize(
                page_id,
                ClassificationStage.HISTORY,
                history_decision,
            )

        merchant_decision = await self._safe_decide(current.description, choices)
        if self._accepted(merchant_decision):
            return await self._finalize(
                page_id,
                ClassificationStage.MERCHANT,
                merchant_decision,
            )

        if self.search is None or self.decider is None:
            return _unresolved(page_id, "Classification remained below confidence threshold.")
        web_context = await self._safe_search(current.description)
        if not web_context:
            return _unresolved(page_id, "No usable public merchant context was found.")
        web_decision = await self._safe_decide(
            current.description,
            choices,
            web_context=web_context,
        )
        if self._accepted(web_decision):
            return await self._finalize(
                page_id,
                ClassificationStage.WEB,
                web_decision,
            )
        return _unresolved(page_id, "Classification remained below confidence threshold.")

    def _accepted(self, decision: CategoryDecision | None) -> bool:
        return (
            decision is not None
            and decision.confidence >= self.policy.confidence_threshold
        )

    async def _safe_decide(
        self,
        merchant: str,
        choices: tuple[CategoryChoice, ...],
        *,
        web_context: tuple[SearchEvidence, ...] = (),
    ) -> CategoryDecision | None:
        if self.decider is None:
            return None
        # A provider failure must not block the already-completed expense write.
        try:
            decision = await self.decider.decide(
                merchant,
                choices,
                web_context=web_context,
            )
        except Exception:
            return None
        if decision is None:
            return None
        allowed = {(item.category, item.subcategory) for item in choices}
        if (decision.category, decision.subcategory) not in allowed:
            return None
        return decision

    async def _safe_search(self, merchant: str) -> tuple[SearchEvidence, ...]:
        if self.search is None:
            return ()
        # A provider failure must not block the already-completed expense write.
        try:
            return await self.search.search(merchant)
        except Exception:
            return ()

    async def _finalize(
        self,
        page_id: str,
        stage: ClassificationStage,
        decision: CategoryDecision | None,
    ) -> ClassificationOutcome:
        if decision is None:  # pragma: no cover - guarded by callers
            return _unresolved(page_id, "Classifier returned no decision.")
        applied = self.policy.mode is ClassificationMode.APPLY
        if applied:
            await self.writer.apply_classification(
                page_id,
                decision.category,
                decision.subcategory,
            )
        return ClassificationOutcome(
            page_id=page_id,
            stage=stage,
            category=decision.category,
            subcategory=decision.subcategory,
            confidence=decision.confidence,
            reason=decision.reason,
            applied=applied,
            would_apply=True,
        )


def evaluate_history_classifier(
    transactions: Iterable[Transaction],
    *,
    policy: ExpenseClassificationPolicy | None = None,
) -> HistoryClassifierEvaluation:
    """Walk forward through labeled history without leaking future transactions."""

    evaluation_policy = policy or ExpenseClassificationPolicy()
    ordered = sorted(transactions, key=lambda item: (item.occurred_on, item.id))
    eligible = 0
    classified = 0
    correct = 0
    prior: list[Transaction] = []
    for current in ordered:
        if _valid_label(current.category) and _valid_label(current.subcategory):
            eligible += 1
            decision = _history_decision(
                current.description,
                tuple(prior),
                score_cutoff=evaluation_policy.fuzzy_score_cutoff,
                match_limit=evaluation_policy.fuzzy_match_limit,
            )
            if (
                decision is not None
                and decision.confidence >= evaluation_policy.confidence_threshold
            ):
                classified += 1
                if (decision.category, decision.subcategory) == (
                    current.category,
                    current.subcategory,
                ):
                    correct += 1
        prior.append(current)
    coverage = _ratio(classified, eligible)
    accuracy = _ratio(correct, classified)
    return HistoryClassifierEvaluation(
        eligible=eligible,
        classified=classified,
        correct=correct,
        coverage=coverage,
        accuracy=accuracy,
    )


def _normalize_merchant(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    tokens = re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
    useful = [
        token
        for token in tokens
        if token not in _NOISE_TOKENS and not token.isdigit()
    ]
    return " ".join(useful)


def _history_decision(
    merchant: str,
    history: tuple[Transaction, ...],
    *,
    score_cutoff: int,
    match_limit: int,
) -> CategoryDecision | None:
    query = _normalize_merchant(merchant)
    if not query:
        return None
    by_merchant: defaultdict[str, list[Transaction]] = defaultdict(list)
    for item in history:
        normalized = _normalize_merchant(item.description)
        if normalized:
            by_merchant[normalized].append(item)

    exact = by_merchant.get(query)
    if exact:
        return _weighted_history_decision(
            ((item, Decimal("1")) for item in exact),
            reason_prefix="Exact normalized merchant match",
        )

    matches = process.extract(
        query,
        tuple(by_merchant),
        scorer=fuzz.WRatio,
        score_cutoff=score_cutoff,
        limit=match_limit,
    )
    weighted: list[tuple[Transaction, Decimal]] = []
    best_score = Decimal("0")
    for normalized, score, _ in matches:
        weight = Decimal(str(score)) / Decimal("100")
        best_score = max(best_score, weight)
        weighted.extend((item, weight) for item in by_merchant[normalized])
    if not weighted:
        return None
    return _weighted_history_decision(
        iter(weighted),
        reason_prefix=f"Fuzzy merchant match ({best_score:.2f} similarity)",
        similarity=best_score,
    )


def _weighted_history_decision(
    rows: Iterable[tuple[Transaction, Decimal]],
    *,
    reason_prefix: str,
    similarity: Decimal = Decimal("1"),
) -> CategoryDecision | None:
    weights: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    count = 0
    for transaction, weight in rows:
        if transaction.category is None or transaction.subcategory is None:
            continue
        weights[(transaction.category, transaction.subcategory)] += weight
        count += 1
    if not weights:
        return None
    (category, subcategory), winning_weight = max(
        weights.items(), key=lambda item: (item[1], item[0])
    )
    total = sum(weights.values(), Decimal("0"))
    confidence = (winning_weight / total * similarity).quantize(_CONFIDENCE_QUANTUM)
    return CategoryDecision(
        category=category,
        subcategory=subcategory,
        confidence=confidence,
        reason=f"{reason_prefix}; dominant pair among {count} historical transactions.",
    )


def _category_choices(history: tuple[Transaction, ...]) -> tuple[CategoryChoice, ...]:
    examples: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    counts: Counter[tuple[str, str]] = Counter()
    for item in history:
        if item.category is None or item.subcategory is None:
            continue
        key = (item.category, item.subcategory)
        counts[key] += 1
        if item.description not in examples[key] and len(examples[key]) < 5:
            examples[key].append(item.description)
    ranked = sorted(counts, key=lambda key: (-counts[key], key))[:255]
    return tuple(
        CategoryChoice(
            category=category,
            subcategory=subcategory,
            examples=tuple(examples[(category, subcategory)]),
            history_count=counts[(category, subcategory)],
        )
        for category, subcategory in ranked
    )


def _usable_history_row(item: Transaction, current: Transaction) -> bool:
    if not _valid_label(item.category) or not _valid_label(item.subcategory):
        return False
    if _valid_label(current.category) and item.category != current.category:
        return False
    return not (
        _valid_label(current.subcategory)
        and item.subcategory != current.subcategory
    )


def _needs_classification(item: Transaction) -> bool:
    return not _valid_label(item.category) or not _valid_label(item.subcategory)


def _has_multiple_labels(item: Transaction) -> bool:
    return len(item.category_options) > 1 or len(item.subcategory_options) > 1


def _valid_label(value: str | None) -> bool:
    return value is not None and value.strip().casefold() not in _EMPTY_LABELS


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


def _empty_outcome(page_id: str, reason: str) -> ClassificationOutcome:
    return ClassificationOutcome(
        page_id=page_id,
        stage=ClassificationStage.SKIPPED,
        category=None,
        subcategory=None,
        confidence=Decimal("0"),
        reason=reason,
        applied=False,
        would_apply=False,
    )


def _unresolved(page_id: str, reason: str) -> ClassificationOutcome:
    return ClassificationOutcome(
        page_id=page_id,
        stage=ClassificationStage.UNRESOLVED,
        category=None,
        subcategory=None,
        confidence=Decimal("0"),
        reason=reason,
        applied=False,
        would_apply=False,
    )
