"""Fresh-read verification and coordination for automatic budget mutations."""

import hashlib
import json
from datetime import date

from app.domain.budget_mutation import (
    BudgetMutationFreshnessError,
    BudgetMutationItem,
    BudgetMutationProposal,
    BudgetMutationResult,
    BudgetMutationStatus,
    BudgetPreferenceReview,
    BudgetPreferenceReviewRejected,
    VerifiedBudgetMutation,
)
from app.domain.models import Budget, ProgressiveMode
from app.domain.money import ZERO, money
from app.domain.monitoring import DailyBudgetMonitoringReport
from app.services.ports import (
    BudgetMutationRepository,
    BudgetPreferenceReviewer,
    DailyBudgetMonitoringRunner,
)
from app.tools.serialization import jsonable


class BudgetMutationService:
    """Verify twice, review preferences, then invoke the narrow Notion writer."""

    def __init__(
        self,
        monitoring: DailyBudgetMonitoringRunner,
        repository: BudgetMutationRepository,
        preference_reviewer: BudgetPreferenceReviewer,
    ) -> None:
        self.monitoring = monitoring
        self.repository = repository
        self.preference_reviewer = preference_reviewer

    async def apply_report(
        self, candidate: DailyBudgetMonitoringReport
    ) -> BudgetMutationResult:
        """Apply a report only if fresh reads and preference review all agree."""

        first_fresh = await self.monitoring.run(candidate.analysis.as_of)
        if _report_fingerprint(candidate) != _report_fingerprint(first_fresh):
            raise BudgetMutationFreshnessError(
                "Daily report changed before budget mutation verification"
            )
        proposal = build_budget_mutation_proposal(first_fresh)
        if proposal is None:
            return BudgetMutationResult(
                status=BudgetMutationStatus.NO_CHANGES,
                operation_id=None,
            )
        if proposal.guidelines:
            review = await self.preference_reviewer.review(proposal)
        else:
            review = BudgetPreferenceReview(
                approved=True,
                summary="No additional human-authored Budget Monitoring guidelines apply.",
                blocking_concerns=(),
                considered_rule_keys=(),
            )
        _verify_preference_review(proposal, review)

        second_fresh = await self.monitoring.run(candidate.analysis.as_of)
        second_proposal = build_budget_mutation_proposal(second_fresh)
        if second_proposal != proposal:
            raise BudgetMutationFreshnessError(
                "Finance data or preferences changed during internal review"
            )
        return await self.repository.apply(
            VerifiedBudgetMutation(proposal=proposal, preference_review=review)
        )


def build_budget_mutation_proposal(
    report: DailyBudgetMonitoringReport,
) -> BudgetMutationProposal | None:
    """Convert an exact deterministic plan into page-level guarded changes."""

    policy = report.inputs.policy
    if not policy.automatic_budget_adjustments_enabled:
        raise BudgetPreferenceReviewRejected(
            "Automatic budget adjustments are not enabled in Financial Rules"
        )
    plan = report.analysis.adjustment_plan
    if plan is None:
        raise BudgetMutationFreshnessError("Income is missing; no mutation can be built")
    if not plan.budget_changes:
        return None
    income = report.analysis.monthly_income
    if income is None or plan.total_budget_after > income:
        raise BudgetMutationFreshnessError("Proposed budgets exceed current-month income")
    budgets = _budget_index(report.inputs.budgets, report.analysis.month)
    protected = {
        value.strip().casefold() for value in policy.protected_subcategories
    }
    items: list[BudgetMutationItem] = []
    for change in plan.budget_changes:
        budget = budgets.get(change.subcategory)
        if budget is None or budget.amount != change.budget_before:
            raise BudgetMutationFreshnessError(
                f"Budget page mismatch for {change.subcategory!r}"
            )
        if change.budget_after < change.budget_before:
            _verify_donor(budget, protected)
        items.append(
            BudgetMutationItem(
                page_id=budget.id,
                subcategory=budget.subcategory,
                month=budget.month,
                amount_before=change.budget_before,
                amount_after=change.budget_after,
                progressive=budget.progressive,
                volatility_percent=budget.volatility_percent,
                baseline_before=budget.baseline_amount,
                last_adjustment_id_before=budget.last_adjustment_id,
                last_adjustment_reason_before=budget.last_adjustment_reason,
                last_adjustment_at_before=budget.last_adjustment_at,
                last_edited_at_before=budget.last_edited_at,
            )
        )
    fingerprint = _report_fingerprint(report)
    operation_id = f"BADJ-{fingerprint[:24].upper()}"
    reason = _reason(report, tuple(items))
    proposal = BudgetMutationProposal(
        operation_id=operation_id,
        report_fingerprint=fingerprint,
        month=report.analysis.month,
        as_of=report.analysis.as_of,
        monthly_income=income,
        total_budget_before=plan.total_budget_before,
        total_budget_after=plan.total_budget_after,
        reason=reason,
        items=tuple(items),
        guidelines=report.inputs.guidelines,
    )
    _verify_proposal_totals(proposal, report)
    return proposal


def _budget_index(budgets: tuple[Budget, ...], month: date) -> dict[str, Budget]:
    result: dict[str, Budget] = {}
    for budget in budgets:
        if budget.month != month:
            continue
        if budget.subcategory in result:
            raise BudgetMutationFreshnessError(
                f"Duplicate budget page for {budget.subcategory!r}"
            )
        result[budget.subcategory] = budget
    return result


def _verify_donor(budget: Budget, protected: set[str]) -> None:
    if budget.subcategory.strip().casefold() in protected:
        raise BudgetMutationFreshnessError("A protected budget cannot donate")
    if budget.progressive is not ProgressiveMode.ACCUMULATED:
        raise BudgetMutationFreshnessError("Only Accumulated budgets may donate")
    if budget.volatility_percent is None or budget.volatility_percent <= ZERO:
        raise BudgetMutationFreshnessError("Donor budget has no controllable volatility")


def _verify_proposal_totals(
    proposal: BudgetMutationProposal, report: DailyBudgetMonitoringReport
) -> None:
    delta = money(
        sum(
            (item.amount_after - item.amount_before for item in proposal.items),
            ZERO,
        )
    )
    expected_total = money(proposal.total_budget_before + delta)
    if expected_total != proposal.total_budget_after:
        raise BudgetMutationFreshnessError("Budget change deltas do not match plan totals")
    plan = report.analysis.adjustment_plan
    if plan is None or tuple(
        (item.subcategory, item.amount_before, item.amount_after)
        for item in proposal.items
    ) != tuple(
        (item.subcategory, item.budget_before, item.budget_after)
        for item in plan.budget_changes
    ):
        raise BudgetMutationFreshnessError("Page-level changes differ from deterministic plan")


def _verify_preference_review(
    proposal: BudgetMutationProposal, review: BudgetPreferenceReview
) -> None:
    expected = {guideline.key for guideline in proposal.guidelines}
    considered = set(review.considered_rule_keys)
    if considered != expected:
        raise BudgetPreferenceReviewRejected(
            "Preference review did not consider every relevant Financial Rule"
        )
    if not review.approved or review.blocking_concerns:
        concerns = "; ".join(review.blocking_concerns) or review.summary
        raise BudgetPreferenceReviewRejected(concerns)


def _report_fingerprint(report: DailyBudgetMonitoringReport) -> str:
    payload = json.dumps(
        jsonable(report),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _reason(
    report: DailyBudgetMonitoringReport, items: tuple[BudgetMutationItem, ...]
) -> str:
    changes = "; ".join(
        f"{item.subcategory}: ₪{item.amount_before} → ₪{item.amount_after}"
        for item in items
    )
    return f"Automatic current-month rebalance on {report.analysis.as_of}: {changes}"
