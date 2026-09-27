"""Pure expense-event diffs, impact measurements, alert policy, and drafts."""

import calendar
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal

from financial_agent.domain.expense_monitoring import (
    AlertState,
    BudgetReallocationChange,
    BudgetReallocationDraft,
    ExpenseAlertDraft,
    ExpenseAlertEvaluation,
    ExpenseChangedEvent,
    ExpenseDelta,
    ExpenseDiff,
    ExpenseImpact,
    ExpenseImpactAssessment,
    ExpenseImpactLine,
    ExpenseSeverity,
    MonitoringDecision,
    ProcessedExpenseSnapshot,
)
from financial_agent.domain.models import Budget, ProgressiveMode, Transaction
from financial_agent.domain.money import ZERO, money
from financial_agent.domain.monitoring import FundingSourceKind, MonitoringInputSnapshot
from financial_agent.services.daily_budget_analysis import analyze_daily_budget_state


def snapshot_transaction(
    transaction: Transaction,
    *,
    processed_at: datetime,
) -> ProcessedExpenseSnapshot:
    """Create the stable page fingerprint stored in the processing ledger."""

    payload = {
        "page_id": transaction.id,
        "description": transaction.description,
        "occurred_on": transaction.occurred_on.isoformat(),
        "final_amount": str(transaction.final_amount),
        "category_options": transaction.category_options,
        "subcategory_options": transaction.subcategory_options,
        "payment_type": transaction.payment_type,
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ProcessedExpenseSnapshot(
        page_id=transaction.id,
        fingerprint=hashlib.sha256(serialized.encode()).hexdigest(),
        final_amount=transaction.final_amount,
        category_options=transaction.category_options,
        subcategory_options=transaction.subcategory_options,
        occurred_on=transaction.occurred_on,
        description=transaction.description,
        payment_type=transaction.payment_type,
        last_processed_at=processed_at,
    )


def resolve_expense_diff(
    event: ExpenseChangedEvent,
    previous: ProcessedExpenseSnapshot | None,
    current: ProcessedExpenseSnapshot | None,
) -> ExpenseDiff:
    """Resolve duplicate, amount, classification, date, and deletion changes."""

    if current is not None and current.page_id != event.notion_page_id:
        raise ValueError("Resolved expense page does not match the trigger page ID")
    if previous is not None and previous.page_id != event.notion_page_id:
        raise ValueError("Ledger snapshot does not match the trigger page ID")
    if previous is None and current is None:
        return ExpenseDiff(event, None, None, (), False, "No current or prior expense exists.")
    if (
        previous is not None
        and current is not None
        and previous.fingerprint == current.fingerprint
    ):
        return ExpenseDiff(
            event,
            previous,
            current,
            (),
            False,
            "The expense fingerprint is already current.",
        )

    deltas: list[ExpenseDelta] = []
    if previous is None:
        assert current is not None
        deltas.append(_delta(current, current.final_amount))
        reason = "New expense added."
    elif current is None:
        deltas.append(_delta(previous, -previous.final_amount))
        reason = "Previously processed expense removed."
    elif _financial_identity(previous) == _financial_identity(current):
        amount_delta = money(current.final_amount - previous.final_amount)
        if amount_delta != ZERO:
            deltas.append(_delta(current, amount_delta))
            reason = "Expense amount changed."
        else:
            reason = "Only non-financial expense fields changed."
    else:
        deltas.extend(
            (
                _delta(previous, -previous.final_amount),
                _delta(current, current.final_amount),
            )
        )
        reason = "Expense date or classification changed; old impact removed and new impact added."
    return ExpenseDiff(event, previous, current, tuple(deltas), True, reason)


def calculate_expense_impact(
    diff: ExpenseDiff,
    snapshots: Mapping[date, MonitoringInputSnapshot],
) -> ExpenseImpact:
    """Calculate exact before/after impact without trusting a model for arithmetic."""

    lines: list[ExpenseImpactLine] = []
    for month in diff.affected_months:
        snapshot = snapshots.get(month)
        if snapshot is None:
            raise ValueError(f"Missing financial snapshot for {month:%Y-%m}")
        budgets = _budget_index(snapshot)
        regular_scopes = {
            item.subcategory
            for item in (diff.previous, diff.current)
            if item is not None
            and item.occurred_on.replace(day=1) == month
            and item.subcategory is not None
            and item.subcategory in budgets
        }
        for subcategory in sorted(regular_scopes, key=str.casefold):
            budget = budgets[subcategory]
            before_amount = _snapshot_amount(diff.previous, month, subcategory, budgets)
            after_amount = _snapshot_amount(diff.current, month, subcategory, budgets)
            other = money(
                sum(
                    (
                        transaction.final_amount
                        for transaction in snapshot.transactions
                        if transaction.id != diff.event.notion_page_id
                        and transaction.subcategory == subcategory
                    ),
                    ZERO,
                )
            )
            lines.append(
                _regular_impact_line(
                    snapshot,
                    budget,
                    other,
                    before_amount,
                    after_amount,
                )
            )

        variable_before = _variable_snapshot_amount(diff.previous, month, budgets)
        variable_after = _variable_snapshot_amount(diff.current, month, budgets)
        if variable_before != ZERO or variable_after != ZERO:
            lines.append(
                _variable_impact_line(
                    snapshot,
                    diff.event.notion_page_id,
                    variable_before,
                    variable_after,
                )
            )

    return ExpenseImpact(
        expense_id=diff.event.notion_page_id,
        event_id=diff.event.event_id,
        lines=tuple(lines),
        net_monthly_spending_change=money(
            sum((delta.amount for delta in diff.deltas), ZERO)
        ),
    )


def classify_expense_impact(
    impact: ExpenseImpact,
    snapshots: Mapping[date, MonitoringInputSnapshot],
) -> tuple[ExpenseImpactAssessment, ...]:
    """Convert measurements to controlled severities."""

    return tuple(
        _classify_line(line, snapshots[line.month]) for line in impact.lines
    )


def evaluate_expense_alert(
    assessment: ExpenseImpactAssessment,
    previous: AlertState | None,
    event: ExpenseChangedEvent,
    snapshot: MonitoringInputSnapshot,
    *,
    record_alert: bool = True,
) -> ExpenseAlertEvaluation:
    """Apply severity escalation, material-change, recovery, and cooldown rules."""

    line = assessment.line
    variance = money(max(line.budget_variance_projection or ZERO, ZERO))
    policy = snapshot.policy
    should_alert = False
    reason = "Severity does not require an alert."
    if _severity_rank(assessment.severity) >= _severity_rank(ExpenseSeverity.WATCH):
        if previous is None:
            should_alert = True
            reason = "First unhealthy observation for this monthly scope."
        elif _severity_rank(assessment.severity) > _severity_rank(previous.last_severity):
            should_alert = True
            reason = "Severity increased."
        elif _severity_rank(previous.last_severity) < _severity_rank(ExpenseSeverity.WATCH):
            should_alert = True
            reason = "The scope recovered and became unhealthy again."
        elif (
            variance - previous.last_projected_variance
            >= policy.material_projection_increase
        ):
            should_alert = True
            reason = "Projected overspend increased materially."
        elif line.protected and assessment.severity is ExpenseSeverity.CRITICAL:
            should_alert = True
            reason = "A protected financial constraint is threatened."
        elif previous.last_alerted_at is None or (
            event.observed_at - previous.last_alerted_at
            >= timedelta(hours=policy.alert_cooldown_hours)
        ):
            should_alert = True
            reason = "The category alert cooldown expired."
        else:
            reason = "An equivalent alert is still inside its cooldown."

    alert = (
        ExpenseAlertDraft(
            event_id=event.event_id,
            month=line.month,
            scope=line.scope,
            severity=assessment.severity,
            projected_variance=variance,
            message=_alert_message(assessment),
            deduplication_reason=reason,
            protected_constraint=line.protected,
        )
        if should_alert
        else None
    )
    last_alerted_at = (
        event.observed_at
        if alert is not None and record_alert
        else previous.last_alerted_at
        if previous is not None
        else None
    )
    return ExpenseAlertEvaluation(
        assessment=assessment,
        alert=alert,
        next_state=AlertState(
            month=line.month,
            scope=line.scope,
            last_severity=assessment.severity,
            last_projected_variance=variance,
            last_evaluated_at=event.observed_at,
            last_alerted_at=last_alerted_at,
        ),
    )


def draft_budget_reallocation(
    diff: ExpenseDiff,
    assessments: Sequence[ExpenseImpactAssessment],
    snapshots: Mapping[date, MonitoringInputSnapshot],
) -> tuple[BudgetReallocationDraft, ...]:
    """Draft bounded donor-to-target changes; never consume reserve or write a page."""

    drafts: list[BudgetReallocationDraft] = []
    for month in diff.affected_months:
        if month != diff.event.observed_at.date().replace(day=1):
            continue
        snapshot = snapshots[month]
        policy = snapshot.policy
        if not policy.reallocation_enabled or snapshot.monthly_income is None:
            continue
        targets = {
            assessment.line.subcategory
            for assessment in assessments
            if assessment.line.month == month
            and assessment.line.subcategory is not None
            and assessment.line.budget is not None
            and assessment.line.spent_before <= assessment.line.budget
            and assessment.line.spent_after > assessment.line.budget
            and _severity_rank(assessment.severity)
            >= _severity_rank(ExpenseSeverity.WARNING)
        }
        if not targets:
            continue
        analysis = analyze_daily_budget_state(
            snapshot.transactions,
            snapshot.budgets,
            snapshot.as_of,
            snapshot.monthly_income,
            policy=policy,
        )
        plan = analysis.adjustment_plan
        if plan is None:
            continue
        remaining = policy.reallocation_max_amount
        net_changes: dict[str, Decimal] = {}
        used = ZERO
        for transfer in plan.transfers:
            if (
                remaining == ZERO
                or transfer.source_kind is not FundingSourceKind.BUDGET
                or transfer.source_subcategory is None
                or transfer.target_subcategory not in targets
            ):
                continue
            amount = money(min(transfer.amount, remaining))
            net_changes[transfer.source_subcategory] = money(
                net_changes.get(transfer.source_subcategory, ZERO) - amount
            )
            net_changes[transfer.target_subcategory] = money(
                net_changes.get(transfer.target_subcategory, ZERO) + amount
            )
            used = money(used + amount)
            remaining = money(remaining - amount)
        if used == ZERO:
            continue
        budget_by_name = _budget_index(snapshot)
        changes = tuple(
            BudgetReallocationChange(
                subcategory=name,
                current=budget_by_name[name].amount,
                proposed=money(budget_by_name[name].amount + delta),
            )
            for name, delta in sorted(net_changes.items(), key=lambda item: item[0].casefold())
            if delta != ZERO
        )
        total = money(sum((change.current for change in changes), ZERO))
        drafts.append(
            BudgetReallocationDraft(
                source_expense_id=diff.event.notion_page_id,
                month=month,
                reason=(
                    f"Expense {diff.event.notion_page_id} pushed "
                    f"{', '.join(sorted(targets))} over allocation; move at most ₪{used}."
                ),
                changes=changes,
                total_before=total,
                total_after=money(sum((change.proposed for change in changes), ZERO)),
            )
        )
    return tuple(drafts)


def build_monitoring_decision(
    diff: ExpenseDiff,
    impact: ExpenseImpact,
    assessments: Sequence[ExpenseImpactAssessment],
    snapshots: Mapping[date, MonitoringInputSnapshot],
    alerts: Sequence[ExpenseAlertDraft],
    reallocations: Sequence[BudgetReallocationDraft],
) -> MonitoringDecision:
    """Build the compact provenance record later conversation reconciliation can use."""

    referenced_budget_ids = tuple(
        sorted(
            {
                budget.id
                for snapshot in snapshots.values()
                for budget in snapshot.budgets
                if any(
                    line.month == snapshot.month
                    and line.subcategory == budget.subcategory
                    for line in impact.lines
                )
                or any(
                    change.subcategory == budget.subcategory
                    and draft.month == snapshot.month
                    for draft in reallocations
                    for change in draft.changes
                )
            }
        )
    )
    rule_keys = tuple(
        sorted(
            {
                source.key
                for snapshot in snapshots.values()
                for source in snapshot.policy_sources
            }
        )
    )
    highest = max(
        (assessment.severity for assessment in assessments),
        key=_severity_rank,
        default=ExpenseSeverity.NONE,
    )
    if not diff.changed:
        decision = "duplicate_or_missing"
    elif not impact.lines:
        decision = "no_financial_impact"
    elif reallocations:
        decision = "alert_and_reallocation_draft" if alerts else "reallocation_draft"
    elif alerts:
        decision = "alert"
    else:
        decision = "observed"
    return MonitoringDecision(
        event_id=diff.event.event_id,
        source_expense_id=diff.event.notion_page_id,
        months=tuple(sorted(snapshots)),
        scopes=tuple(sorted({line.scope for line in impact.lines}, key=str.casefold)),
        referenced_budget_ids=referenced_budget_ids,
        referenced_rule_keys=rule_keys,
        calculation_version=impact.calculation_version,
        decision=decision,
        highest_severity=highest,
        summary=_decision_summary(diff, assessments, alerts, reallocations),
        created_at=diff.event.observed_at,
    )


def _delta(snapshot: ProcessedExpenseSnapshot, amount: Decimal) -> ExpenseDelta:
    return ExpenseDelta(
        month=snapshot.occurred_on.replace(day=1),
        occurred_on=snapshot.occurred_on,
        subcategory=snapshot.subcategory,
        subcategory_options=snapshot.subcategory_options,
        amount=amount,
    )


def _financial_identity(snapshot: ProcessedExpenseSnapshot) -> tuple[object, ...]:
    return (
        snapshot.occurred_on,
        snapshot.category_options,
        snapshot.subcategory_options,
    )


def _budget_index(snapshot: MonitoringInputSnapshot) -> dict[str, Budget]:
    result: dict[str, Budget] = {}
    for budget in snapshot.budgets:
        if budget.month != snapshot.month:
            continue
        if budget.subcategory in result:
            raise ValueError(
                f"Duplicate budget for {budget.subcategory!r} in {snapshot.month:%Y-%m}"
            )
        result[budget.subcategory] = budget
    return result


def _snapshot_amount(
    snapshot: ProcessedExpenseSnapshot | None,
    month: date,
    subcategory: str,
    budgets: Mapping[str, Budget],
) -> Decimal:
    if (
        snapshot is None
        or snapshot.occurred_on.replace(day=1) != month
        or snapshot.subcategory != subcategory
        or snapshot.subcategory not in budgets
    ):
        return ZERO
    return snapshot.final_amount


def _variable_snapshot_amount(
    snapshot: ProcessedExpenseSnapshot | None,
    month: date,
    budgets: Mapping[str, Budget],
) -> Decimal:
    if snapshot is None or snapshot.occurred_on.replace(day=1) != month:
        return ZERO
    if snapshot.subcategory is not None and snapshot.subcategory in budgets:
        return ZERO
    return snapshot.final_amount


def _regular_impact_line(
    snapshot: MonitoringInputSnapshot,
    budget: Budget,
    other_spend: Decimal,
    before_amount: Decimal,
    after_amount: Decimal,
) -> ExpenseImpactLine:
    spent_before = money(other_spend + before_amount)
    spent_after = money(other_spend + after_amount)
    delta = money(after_amount - before_amount)
    if budget.progressive is ProgressiveMode.ACCUMULATED:
        days = calendar.monthrange(snapshot.as_of.year, snapshot.as_of.month)[1]
        expected = money(budget.amount * Decimal(snapshot.as_of.day) / Decimal(days))
        projected = money(spent_after * Decimal(days) / Decimal(snapshot.as_of.day))
    else:
        expected = None
        projected = money(max(spent_after, budget.amount))
    return ExpenseImpactLine(
        month=snapshot.month,
        as_of=snapshot.as_of,
        subcategory=budget.subcategory,
        scope=budget.subcategory,
        expense_class="regular",
        progressive=budget.progressive,
        budget=budget.amount,
        spent_before=spent_before,
        expense_delta=delta,
        spent_after=spent_after,
        expected_spend_by_today=expected,
        projected_month_end=projected,
        budget_variance_projection=money(projected - budget.amount),
        volatility_percent=budget.volatility_percent,
        protected=budget.subcategory.strip().casefold()
        in {
            value.strip().casefold()
            for value in snapshot.policy.protected_subcategories
        },
    )


def _variable_impact_line(
    snapshot: MonitoringInputSnapshot,
    expense_id: str,
    before_amount: Decimal,
    after_amount: Decimal,
) -> ExpenseImpactLine:
    budgets = _budget_index(snapshot)
    other_variable = money(
        sum(
            (
                transaction.final_amount
                for transaction in snapshot.transactions
                if transaction.id != expense_id
                and (
                    transaction.subcategory is None
                    or transaction.subcategory not in budgets
                )
            ),
            ZERO,
        )
    )
    variable_before = money(other_variable + before_amount)
    variable_after = money(other_variable + after_amount)
    total_budget = money(sum((budget.amount for budget in budgets.values()), ZERO))
    income = snapshot.monthly_income
    pool = (
        money(income - total_budget - snapshot.policy.emergency_buffer_amount)
        if income is not None
        else None
    )
    return ExpenseImpactLine(
        month=snapshot.month,
        as_of=snapshot.as_of,
        subcategory=None,
        scope="Variable",
        expense_class="variable",
        progressive=None,
        budget=None,
        spent_before=variable_before,
        expense_delta=money(after_amount - before_amount),
        spent_after=variable_after,
        expected_spend_by_today=None,
        projected_month_end=None,
        budget_variance_projection=(
            money(variable_after - pool) if pool is not None else None
        ),
        volatility_percent=None,
        variable_pool_before=(money(pool - variable_before) if pool is not None else None),
        variable_pool_after=(money(pool - variable_after) if pool is not None else None),
    )


def _classify_line(
    line: ExpenseImpactLine,
    snapshot: MonitoringInputSnapshot,
) -> ExpenseImpactAssessment:
    policy = snapshot.policy
    material = policy.minimum_material_amount
    if line.expense_delta <= ZERO and (
        line.budget_variance_projection is None
        or line.budget_variance_projection <= ZERO
    ):
        return ExpenseImpactAssessment(
            line,
            ExpenseSeverity.NONE,
            "The change improves or preserves the scope.",
        )
    if line.expense_class == "variable":
        remaining = line.variable_pool_after
        if remaining is None:
            return ExpenseImpactAssessment(
                line,
                ExpenseSeverity.INFORMATIONAL,
                "Recorded income is missing, so variable-pool safety cannot be verified.",
            )
        if remaining < ZERO:
            severity = (
                ExpenseSeverity.CRITICAL
                if -remaining >= max(material * Decimal("4"), Decimal("100.00"))
                else ExpenseSeverity.WARNING
            )
            return ExpenseImpactAssessment(
                line,
                severity,
                f"The remaining variable pool is ₪{remaining}.",
            )
        return ExpenseImpactAssessment(
            line,
            ExpenseSeverity.NONE,
            "The remaining variable pool absorbs this change.",
        )

    assert line.budget is not None
    variance = money(line.budget_variance_projection or ZERO)
    if line.spent_after > line.budget:
        severity = ExpenseSeverity.CRITICAL
        reason = "Actual spending already exceeds the full allocation."
    elif variance <= ZERO or variance < material:
        severity = ExpenseSeverity.NONE
        reason = "Spending remains inside the configured materiality band."
    elif line.progressive is not ProgressiveMode.ACCUMULATED:
        severity = ExpenseSeverity.INFORMATIONAL
        reason = "Discrete spending is evaluated against the full monthly allocation."
    else:
        percent = variance / line.budget * Decimal("100") if line.budget > ZERO else Decimal("100")
        crossed = sum(percent >= band for band in policy.projection_bands)
        severity = (
            ExpenseSeverity.CRITICAL
            if crossed >= 3
            else ExpenseSeverity.WARNING
            if crossed == 2
            else ExpenseSeverity.WATCH
            if crossed == 1
            else ExpenseSeverity.INFORMATIONAL
        )
        reason = f"Projected month-end spending is ₪{variance} above allocation."
    if line.protected and _severity_rank(severity) >= _severity_rank(ExpenseSeverity.WARNING):
        severity = ExpenseSeverity.CRITICAL
        reason = f"Protected scope: {reason}"
    return ExpenseImpactAssessment(line, severity, reason)


def _alert_message(assessment: ExpenseImpactAssessment) -> str:
    line = assessment.line
    prefix = assessment.severity.value.capitalize()
    if line.expense_class == "variable":
        return (
            f"{prefix}: this expense leaves the {line.month:%Y-%m} variable pool at "
            f"₪{line.variable_pool_after}. {assessment.reason}"
        )
    return (
        f"{prefix}: {line.scope} is at ₪{line.spent_after} and projects to "
        f"₪{line.projected_month_end} against a ₪{line.budget} budget. "
        f"{assessment.reason}"
    )


def _severity_rank(value: ExpenseSeverity) -> int:
    return {
        ExpenseSeverity.NONE: 0,
        ExpenseSeverity.INFORMATIONAL: 1,
        ExpenseSeverity.WATCH: 2,
        ExpenseSeverity.WARNING: 3,
        ExpenseSeverity.CRITICAL: 4,
    }[value]


def _decision_summary(
    diff: ExpenseDiff,
    assessments: Sequence[ExpenseImpactAssessment],
    alerts: Sequence[ExpenseAlertDraft],
    reallocations: Sequence[BudgetReallocationDraft],
) -> str:
    material = [
        _alert_message(assessment)
        for assessment in assessments
        if assessment.severity is not ExpenseSeverity.NONE
    ]
    if not material:
        material.append(f"{diff.reason} No material budget impact.")
    for draft in reallocations:
        changes = "; ".join(
            f"{change.subcategory} ₪{change.current} → ₪{change.proposed}"
            for change in draft.changes
        )
        material.append(f"Safe reallocation available: {changes}.")
    return " ".join(material)
