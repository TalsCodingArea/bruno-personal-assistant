"""Pure daily budget analysis with no Notion, graph, or notification I/O."""

import calendar
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.domain.models import Budget, ProgressiveMode, Transaction
from app.domain.money import ZERO, money
from app.domain.monitoring import (
    AnalysisStatus,
    BudgetAdjustmentPlan,
    BudgetAlertDraft,
    BudgetChange,
    BudgetFundingTransfer,
    BudgetObservation,
    CategoryBudgetAnalysis,
    DailyBudgetAnalysis,
    FundingSourceKind,
    MonitoringPolicy,
    ObservationKind,
    OverspendResolution,
)

PERCENT = Decimal("0.01")


def analyze_daily_budget_state(
    transactions: tuple[Transaction, ...] | list[Transaction],
    budgets: tuple[Budget, ...] | list[Budget],
    as_of: date,
    monthly_income: Decimal | None,
    *,
    policy: MonitoringPolicy | None = None,
) -> DailyBudgetAnalysis:
    """Analyze pacing, variable reserve, and bounded current-month adjustments."""

    effective_policy = policy or MonitoringPolicy()
    month = as_of.replace(day=1)
    monthly_budgets = _monthly_budgets(budgets, month)
    budget_by_subcategory = {budget.subcategory: budget for budget in monthly_budgets}
    actual_by_subcategory: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
    variable_spend = ZERO
    uncategorized_spend = ZERO

    for transaction in transactions:
        if not _is_observed_this_month(transaction, month, as_of):
            continue
        subcategory = transaction.subcategory
        if subcategory is not None and subcategory in budget_by_subcategory:
            actual_by_subcategory[subcategory] += transaction.final_amount
        else:
            variable_spend += transaction.final_amount
            if subcategory is None:
                uncategorized_spend += transaction.final_amount

    categories = tuple(
        _analyze_category(
            budget,
            money(actual_by_subcategory[budget.subcategory]),
            as_of,
            effective_policy,
        )
        for budget in monthly_budgets
    )
    total_budget = money(sum((budget.amount for budget in monthly_budgets), ZERO))
    variable_spend = money(variable_spend)
    uncategorized_spend = money(uncategorized_spend)
    observations = list(_projection_observations(categories, month, as_of))

    if monthly_income is None:
        observations.append(
            BudgetObservation(
                kind=ObservationKind.INCOME_MISSING,
                month=month,
                as_of=as_of,
            )
        )
        alerts = _alert_drafts_without_adjustments(categories)
        return DailyBudgetAnalysis(
            status=AnalysisStatus.INCOME_MISSING,
            month=month,
            as_of=as_of,
            monthly_income=None,
            total_budget=total_budget,
            starting_variable_reserve=None,
            actual_variable_spend=variable_spend,
            uncategorized_spend=uncategorized_spend,
            remaining_variable_reserve_before_adjustment=None,
            categories=categories,
            observations=tuple(observations),
            adjustment_plan=None,
            alerts=alerts,
        )

    income = money(monthly_income)
    if income < ZERO:
        raise ValueError("monthly_income cannot be negative")
    starting_reserve = money(income - total_budget)
    remaining_reserve = money(starting_reserve - variable_spend)
    if starting_reserve < ZERO:
        observations.append(
            BudgetObservation(
                kind=ObservationKind.BUDGETS_EXCEED_INCOME,
                month=month,
                as_of=as_of,
                amount=money(-starting_reserve),
            )
        )
    if remaining_reserve < ZERO:
        observations.append(
            BudgetObservation(
                kind=ObservationKind.VARIABLE_RESERVE_DEFICIT,
                month=month,
                as_of=as_of,
                amount=money(-remaining_reserve),
            )
        )

    plan = _build_adjustment_plan(
        categories,
        month,
        as_of,
        income,
        variable_spend,
        starting_reserve,
        remaining_reserve,
        effective_policy,
    )
    resolutions = {item.subcategory: item for item in plan.overspend_resolutions}
    changes = {item.subcategory: item for item in plan.budget_changes}
    alerts = tuple(
        BudgetAlertDraft(
            subcategory=category.subcategory,
            actual_overspend=category.actual_overspend,
            adjustment_amount=money(
                changes.get(
                    category.subcategory,
                    BudgetChange(category.subcategory, category.budget, category.budget),
                ).budget_after
                - category.budget
            ),
            unresolved_amount=resolutions[category.subcategory].unresolved,
        )
        for category in categories
        if category.actual_overspend > ZERO
    )
    return DailyBudgetAnalysis(
        status=AnalysisStatus.READY,
        month=month,
        as_of=as_of,
        monthly_income=income,
        total_budget=total_budget,
        starting_variable_reserve=starting_reserve,
        actual_variable_spend=variable_spend,
        uncategorized_spend=uncategorized_spend,
        remaining_variable_reserve_before_adjustment=remaining_reserve,
        categories=categories,
        observations=tuple(observations),
        adjustment_plan=plan,
        alerts=alerts,
    )


def _monthly_budgets(budgets: tuple[Budget, ...] | list[Budget], month: date) -> list[Budget]:
    result: list[Budget] = []
    seen: set[str] = set()
    for budget in budgets:
        if budget.month != month:
            continue
        if budget.subcategory in seen:
            raise ValueError(f"Duplicate budget for subcategory: {budget.subcategory}")
        seen.add(budget.subcategory)
        result.append(budget)
    return sorted(result, key=lambda budget: budget.subcategory.casefold())


def _is_observed_this_month(transaction: Transaction, month: date, as_of: date) -> bool:
    return transaction.occurred_on.replace(day=1) == month and transaction.occurred_on <= as_of


def _analyze_category(
    budget: Budget,
    actual: Decimal,
    as_of: date,
    policy: MonitoringPolicy,
) -> CategoryBudgetAnalysis:
    projected = _projected_spend(budget, actual, as_of)
    overspend = money(max(actual - budget.amount, ZERO))
    deviation = _positive_deviation_percent(projected, budget.amount)
    band = _highest_crossed_band(deviation, policy.projection_bands)
    return CategoryBudgetAnalysis(
        subcategory=budget.subcategory,
        budget=budget.amount,
        actual_spend=actual,
        projected_spend=projected,
        actual_overspend=overspend,
        projection_deviation_percent=deviation,
        projection_band=band,
        progressive=budget.progressive,
        volatility_percent=budget.volatility_percent,
        protected=_normalized(budget.subcategory)
        in {_normalized(value) for value in policy.protected_subcategories},
    )


def _projected_spend(budget: Budget, actual: Decimal, as_of: date) -> Decimal:
    if budget.progressive is ProgressiveMode.ACCUMULATED:
        days_in_month = calendar.monthrange(as_of.year, as_of.month)[1]
        return money(actual * Decimal(days_in_month) / Decimal(as_of.day))
    return money(max(actual, budget.amount))


def _positive_deviation_percent(projected: Decimal, budget: Decimal) -> Decimal | None:
    if budget == ZERO:
        return None
    deviation = (projected - budget) / budget * Decimal("100")
    return max(deviation, ZERO).quantize(PERCENT, rounding=ROUND_HALF_UP)


def _highest_crossed_band(
    deviation: Decimal | None, bands: tuple[Decimal, ...]
) -> Decimal | None:
    if deviation is None:
        return None
    crossed = tuple(band for band in bands if deviation >= band)
    return crossed[-1] if crossed else None


def _projection_observations(
    categories: tuple[CategoryBudgetAnalysis, ...], month: date, as_of: date
) -> tuple[BudgetObservation, ...]:
    return tuple(
        BudgetObservation(
            kind=ObservationKind.PROJECTION_DEVIATION,
            month=month,
            as_of=as_of,
            subcategory=category.subcategory,
            amount=money(category.projected_spend - category.budget),
            percent=category.projection_deviation_percent,
            band=category.projection_band,
        )
        for category in categories
        if category.projection_band is not None
    )


def _build_adjustment_plan(
    categories: tuple[CategoryBudgetAnalysis, ...],
    month: date,
    as_of: date,
    income: Decimal,
    variable_spend: Decimal,
    starting_reserve: Decimal,
    remaining_reserve: Decimal,
    policy: MonitoringPolicy,
) -> BudgetAdjustmentPlan:
    original = {category.subcategory: category.budget for category in categories}
    current = dict(original)
    donor_capacity = {
        category.subcategory: _donor_capacity(category) for category in categories
    }
    donor_order = _donor_order(categories, donor_capacity, policy)
    transfers: list[BudgetFundingTransfer] = []

    variable_deficit = money(max(-remaining_reserve, ZERO))
    unresolved_variable = _fund_from_donors(
        variable_deficit,
        None,
        donor_order,
        donor_capacity,
        current,
        transfers,
    )
    available_reserve = money(max(remaining_reserve, ZERO))
    resolutions: list[OverspendResolution] = []

    for target in sorted(categories, key=_target_priority):
        if target.actual_overspend == ZERO:
            continue
        required = target.actual_overspend
        from_reserve = money(min(required, available_reserve))
        if from_reserve > ZERO:
            transfers.append(
                BudgetFundingTransfer(
                    source_kind=FundingSourceKind.VARIABLE_RESERVE,
                    target_subcategory=target.subcategory,
                    amount=from_reserve,
                )
            )
            current[target.subcategory] = money(current[target.subcategory] + from_reserve)
            available_reserve = money(available_reserve - from_reserve)
            required = money(required - from_reserve)

        unresolved = _fund_from_donors(
            required,
            target.subcategory,
            donor_order,
            donor_capacity,
            current,
            transfers,
        )
        from_budgets = money(required - unresolved)
        if from_budgets > ZERO:
            current[target.subcategory] = money(
                current[target.subcategory] + from_budgets
            )
        resolutions.append(
            OverspendResolution(
                subcategory=target.subcategory,
                overspend=target.actual_overspend,
                funded_from_variable_reserve=from_reserve,
                funded_from_budgets=from_budgets,
                unresolved=unresolved,
            )
        )

    changes = tuple(
        BudgetChange(
            subcategory=subcategory,
            budget_before=original[subcategory],
            budget_after=current[subcategory],
        )
        for subcategory in sorted(original, key=str.casefold)
        if current[subcategory] != original[subcategory]
    )
    total_before = money(sum(original.values(), ZERO))
    total_after = money(sum(current.values(), ZERO))
    reserve_after = money(income - total_after - variable_spend)
    unresolved_budget = money(
        sum((resolution.unresolved for resolution in resolutions), ZERO)
    )
    return BudgetAdjustmentPlan(
        month=month,
        as_of=as_of,
        total_budget_before=total_before,
        total_budget_after=total_after,
        starting_variable_reserve=starting_reserve,
        variable_spend=variable_spend,
        remaining_variable_reserve_before_adjustment=remaining_reserve,
        remaining_variable_reserve_after_adjustment=reserve_after,
        unresolved_variable_deficit=unresolved_variable,
        unresolved_budget_shortfall=unresolved_budget,
        transfers=tuple(transfers),
        budget_changes=changes,
        overspend_resolutions=tuple(resolutions),
    )


def _donor_capacity(category: CategoryBudgetAnalysis) -> Decimal:
    volatility = category.volatility_percent
    if category.protected or volatility is None or volatility <= ZERO:
        return ZERO
    if category.progressive is not ProgressiveMode.ACCUMULATED:
        return ZERO
    required = max(category.actual_spend, category.projected_spend)
    headroom = max(category.budget - required, ZERO)
    return money(headroom * volatility / Decimal("100"))


def _donor_order(
    categories: tuple[CategoryBudgetAnalysis, ...],
    capacities: dict[str, Decimal],
    policy: MonitoringPolicy,
) -> tuple[str, ...]:
    preference = {
        _normalized(subcategory): index
        for index, subcategory in enumerate(policy.preferred_donor_subcategories)
    }
    fallback_rank = len(preference)

    def key(category: CategoryBudgetAnalysis) -> tuple[int, Decimal, Decimal, str]:
        volatility = category.volatility_percent or ZERO
        return (
            preference.get(_normalized(category.subcategory), fallback_rank),
            -volatility,
            -capacities[category.subcategory],
            category.subcategory.casefold(),
        )

    eligible = (
        category for category in categories if capacities[category.subcategory] > ZERO
    )
    return tuple(category.subcategory for category in sorted(eligible, key=key))


def _fund_from_donors(
    requested: Decimal,
    target_subcategory: str | None,
    donor_order: tuple[str, ...],
    donor_capacity: dict[str, Decimal],
    current_budgets: dict[str, Decimal],
    transfers: list[BudgetFundingTransfer],
) -> Decimal:
    remaining = money(requested)
    for donor in donor_order:
        if remaining == ZERO:
            break
        if donor == target_subcategory:
            continue
        amount = money(min(remaining, donor_capacity[donor]))
        if amount == ZERO:
            continue
        donor_capacity[donor] = money(donor_capacity[donor] - amount)
        current_budgets[donor] = money(current_budgets[donor] - amount)
        transfers.append(
            BudgetFundingTransfer(
                source_kind=FundingSourceKind.BUDGET,
                source_subcategory=donor,
                target_subcategory=target_subcategory,
                amount=amount,
            )
        )
        remaining = money(remaining - amount)
    return remaining


def _target_priority(category: CategoryBudgetAnalysis) -> tuple[int, int, Decimal, str]:
    if category.budget == ZERO:
        severity = Decimal("0")
        zero_budget_rank = 0
    else:
        severity = category.actual_overspend / category.budget
        zero_budget_rank = 1
    return (
        0 if category.protected else 1,
        zero_budget_rank,
        -severity,
        category.subcategory.casefold(),
    )


def _alert_drafts_without_adjustments(
    categories: tuple[CategoryBudgetAnalysis, ...],
) -> tuple[BudgetAlertDraft, ...]:
    return tuple(
        BudgetAlertDraft(
            subcategory=category.subcategory,
            actual_overspend=category.actual_overspend,
            adjustment_amount=ZERO,
            unresolved_amount=category.actual_overspend,
        )
        for category in categories
        if category.actual_overspend > ZERO
    )


def _normalized(value: str) -> str:
    return value.strip().casefold()
