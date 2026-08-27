"""Adversarial tests for fresh-read and preference-gated budget mutation."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.budget_mutation import (
    BudgetMutationFreshnessError,
    BudgetMutationResult,
    BudgetMutationStatus,
    BudgetPreferenceReview,
    BudgetPreferenceReviewRejected,
)
from app.domain.models import Budget, Income, ProgressiveMode, Transaction
from app.domain.monitoring import (
    DailyBudgetMonitoringReport,
    MonitoringGuideline,
    MonitoringInputSnapshot,
    MonitoringPolicy,
)
from app.services.budget_mutation import (
    BudgetMutationService,
    build_budget_mutation_proposal,
)
from app.services.daily_budget_analysis import analyze_daily_budget_state

AS_OF = date(2026, 8, 20)
EDITED = datetime(2026, 8, 20, 3, 55, tzinfo=UTC)


def monitoring_report(
    *,
    groceries_spend: str = "130",
    automatic: bool = True,
    guidelines: tuple[MonitoringGuideline, ...] = (),
) -> DailyBudgetMonitoringReport:
    transactions = (
        Transaction(
            "expense",
            "Market",
            AS_OF,
            Decimal(groceries_spend),
            category="Food",
            subcategory="Groceries",
        ),
    )
    incomes = (Income("salary", "Salary", date(2026, 8, 1), Decimal("600")),)
    budgets = (
        Budget(
            "groceries-page",
            "Groceries",
            date(2026, 8, 1),
            Decimal("100"),
            ProgressiveMode.ACCUMULATED,
            Decimal("50"),
            last_edited_at=EDITED,
        ),
        Budget(
            "entertainment-page",
            "Entertainment",
            date(2026, 8, 1),
            Decimal("400"),
            ProgressiveMode.ACCUMULATED,
            Decimal("50"),
            last_edited_at=EDITED,
        ),
    )
    policy = MonitoringPolicy(automatic_budget_adjustments_enabled=automatic)
    snapshot = MonitoringInputSnapshot(
        month=date(2026, 8, 1),
        as_of=AS_OF,
        transactions=transactions,
        incomes=incomes,
        budgets=budgets,
        monthly_income=Decimal("600"),
        policy=policy,
        policy_sources=(),
        guidelines=guidelines,
    )
    analysis = analyze_daily_budget_state(
        transactions,
        budgets,
        AS_OF,
        Decimal("600"),
        policy=policy,
    )
    return DailyBudgetMonitoringReport(inputs=snapshot, analysis=analysis)


def guideline(key: str = "budgeting.takeout_preference") -> MonitoringGuideline:
    return MonitoringGuideline(
        key=key,
        statement="Prefer reducing Entertainment before Groceries.",
        kind="Preference",
        scopes=("Budget Monitoring",),
        page_id=f"page-{key}",
        operation_id="CTX-88",
        last_edited_at=EDITED,
    )


class FakeMonitoringRunner:
    def __init__(self, reports: tuple[DailyBudgetMonitoringReport, ...]) -> None:
        self.reports = list(reports)
        self.calls: list[date] = []

    async def run(self, as_of: date) -> DailyBudgetMonitoringReport:
        self.calls.append(as_of)
        return self.reports.pop(0)


class FakePreferenceReviewer:
    def __init__(self, review: BudgetPreferenceReview) -> None:
        self.result = review
        self.calls = 0

    async def review(self, proposal):  # type: ignore[no-untyped-def]
        self.calls += 1
        return self.result


class FakeMutationRepository:
    def __init__(self) -> None:
        self.calls = 0

    async def apply(self, mutation):  # type: ignore[no-untyped-def]
        self.calls += 1
        return BudgetMutationResult(
            status=BudgetMutationStatus.APPLIED,
            operation_id=mutation.proposal.operation_id,
        )


def approved_review(*keys: str) -> BudgetPreferenceReview:
    return BudgetPreferenceReview(
        approved=True,
        summary="No preference conflict.",
        blocking_concerns=(),
        considered_rule_keys=tuple(keys),
    )


def test_automatic_mutation_requires_durable_rule_authorization() -> None:
    with pytest.raises(BudgetPreferenceReviewRejected, match="not enabled"):
        build_budget_mutation_proposal(monitoring_report(automatic=False))


def test_service_rechecks_report_before_and_after_preference_review() -> None:
    rule = guideline()
    candidate = monitoring_report(guidelines=(rule,))
    runner = FakeMonitoringRunner((candidate, candidate))
    reviewer = FakePreferenceReviewer(approved_review(rule.key))
    repository = FakeMutationRepository()

    result = asyncio.run(
        BudgetMutationService(runner, repository, reviewer).apply_report(candidate)
    )

    assert result.status is BudgetMutationStatus.APPLIED
    assert runner.calls == [AS_OF, AS_OF]
    assert reviewer.calls == 1
    assert repository.calls == 1


def test_stale_candidate_is_rejected_before_model_or_writer() -> None:
    candidate = monitoring_report(groceries_spend="130")
    changed = monitoring_report(groceries_spend="131")
    runner = FakeMonitoringRunner((changed,))
    reviewer = FakePreferenceReviewer(approved_review())
    repository = FakeMutationRepository()

    with pytest.raises(BudgetMutationFreshnessError, match="changed before"):
        asyncio.run(
            BudgetMutationService(runner, repository, reviewer).apply_report(candidate)
        )

    assert reviewer.calls == 0
    assert repository.calls == 0


def test_preference_review_must_consider_every_scoped_rule() -> None:
    rule = guideline()
    candidate = monitoring_report(guidelines=(rule,))
    runner = FakeMonitoringRunner((candidate,))
    reviewer = FakePreferenceReviewer(approved_review())
    repository = FakeMutationRepository()

    with pytest.raises(BudgetPreferenceReviewRejected, match="every relevant"):
        asyncio.run(
            BudgetMutationService(runner, repository, reviewer).apply_report(candidate)
        )

    assert repository.calls == 0


def test_model_veto_never_reaches_budget_writer() -> None:
    rule = guideline()
    candidate = monitoring_report(guidelines=(rule,))
    runner = FakeMonitoringRunner((candidate,))
    reviewer = FakePreferenceReviewer(
        BudgetPreferenceReview(
            approved=False,
            summary="Conflicts with user preference.",
            blocking_concerns=("Entertainment must remain unchanged this month.",),
            considered_rule_keys=(rule.key,),
        )
    )
    repository = FakeMutationRepository()

    with pytest.raises(BudgetPreferenceReviewRejected, match="Entertainment"):
        asyncio.run(
            BudgetMutationService(runner, repository, reviewer).apply_report(candidate)
        )

    assert repository.calls == 0


def test_change_during_model_review_invalidates_approved_proposal() -> None:
    rule = guideline()
    candidate = monitoring_report(guidelines=(rule,))
    changed = monitoring_report(groceries_spend="131", guidelines=(rule,))
    runner = FakeMonitoringRunner((candidate, changed))
    reviewer = FakePreferenceReviewer(approved_review(rule.key))
    repository = FakeMutationRepository()

    with pytest.raises(BudgetMutationFreshnessError, match="during internal review"):
        asyncio.run(
            BudgetMutationService(runner, repository, reviewer).apply_report(candidate)
        )

    assert repository.calls == 0


def test_no_budget_changes_skips_model_and_writer() -> None:
    candidate = monitoring_report(groceries_spend="50")
    runner = FakeMonitoringRunner((candidate,))
    reviewer = FakePreferenceReviewer(approved_review())
    repository = FakeMutationRepository()

    result = asyncio.run(
        BudgetMutationService(runner, repository, reviewer).apply_report(candidate)
    )

    assert result.status is BudgetMutationStatus.NO_CHANGES
    assert reviewer.calls == 0
    assert repository.calls == 0
