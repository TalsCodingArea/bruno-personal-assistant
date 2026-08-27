"""Grounded budget-planning context, draft validation, and fresh apply coordination."""

import asyncio
import calendar
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from app.domain.budget_planning import (
    BudgetCapBasis,
    BudgetPageDraft,
    BudgetPlanCreationResult,
    BudgetPlanningContext,
    BudgetPlanningFreshnessError,
    BudgetPlanningGuideline,
    BudgetPlanPurpose,
    BudgetStabilityComparison,
    CreatedBudgetPage,
    FutureExpenseBudgetNeed,
    MonthlyBudgetPlanDraft,
)
from app.domain.interaction import is_interaction_entry
from app.domain.models import Budget, Income, PlannedExpense
from app.domain.money import ZERO, money
from app.domain.operational_context import is_operational_context_entry
from app.services.calculations import planned_expense_monthly_allocation
from app.services.ports import (
    ActiveFinancialRulesReader,
    BudgetPlanCreationRepository,
    FinanceReader,
)


class BudgetPlanningService:
    """Give the model evidence and enforce safety without choosing its budget strategy."""

    def __init__(
        self,
        finance: FinanceReader,
        rules: ActiveFinancialRulesReader,
        repository: BudgetPlanCreationRepository,
        *,
        max_rule_entries: int = 100,
    ) -> None:
        self.finance = finance
        self.rules = rules
        self.repository = repository
        self.max_rule_entries = max_rule_entries

    async def planning_context(self, target_month: date) -> BudgetPlanningContext:
        """Load current/prior budgets, income, future needs, and approved guidance."""

        target = target_month.replace(day=1)
        previous = _add_months(target, -1)
        target_end = _month_end(target)
        previous_end = _month_end(previous)
        future_end = _month_end(_add_months(target, 2))
        (
            target_budgets,
            previous_budgets,
            target_incomes,
            previous_incomes,
            entries,
        ) = await asyncio.gather(
            self.finance.budgets(target),
            self.finance.budgets(previous),
            self.finance.incomes(target, target_end),
            self.finance.incomes(previous, previous_end),
            self.rules.active_entries(limit=self.max_rule_entries),
        )
        try:
            planned = await self.finance.planned_expenses(target, future_end, limit=100)
            future_expenses = tuple(
                sorted(
                    (
                        _future_need(
                            item.id,
                            item.name,
                            item.due_date,
                            item.target_amount,
                            item.saved_amount,
                            target,
                        )
                        for item in planned
                    ),
                    key=lambda item: (
                        item.due_date,
                        item.name.casefold(),
                        item.planned_expense_id,
                    ),
                )
            )
            future_status = "ready"
        except NotImplementedError:
            future_expenses = ()
            future_status = "schema_not_configured"

        guidelines = tuple(
            sorted(
                (
                    BudgetPlanningGuideline(
                        key=entry.key,
                        statement=entry.statement,
                        kind=entry.kind.value,
                        scopes=entry.scopes,
                        page_id=entry.page_id,
                        operation_id=entry.operation_id,
                        last_edited_at=entry.last_edited_at,
                    )
                    for entry in entries
                    if not is_interaction_entry(entry)
                    and not is_operational_context_entry(entry)
                    and any(_budget_scope(scope) for scope in entry.scopes)
                ),
                key=lambda item: (item.key.casefold(), item.page_id),
            )
        )
        sorted_previous_budgets = tuple(
            sorted(previous_budgets, key=lambda item: item.subcategory.casefold())
        )
        target_income = _income_total(target_incomes)
        previous_income = _income_total(previous_incomes)
        source_fingerprint = _source_fingerprint(
            sorted_previous_budgets,
            target_income,
            previous_income,
            future_expenses,
            future_status,
            guidelines,
        )
        return BudgetPlanningContext(
            target_month=target,
            previous_month=previous,
            existing_target_budgets=tuple(
                sorted(target_budgets, key=lambda item: item.subcategory.casefold())
            ),
            previous_budgets=sorted_previous_budgets,
            target_month_income=target_income,
            previous_month_income=previous_income,
            future_expenses=future_expenses,
            future_expenses_status=future_status,
            guidelines=guidelines,
            source_fingerprint=source_fingerprint,
        )

    def draft_plan(
        self,
        context: BudgetPlanningContext,
        items: Sequence[BudgetPageDraft],
        *,
        financial_cap: Decimal,
        cap_basis: BudgetCapBasis,
        cap_rationale: str,
        income_assumption: Decimal | None = None,
        future_expense_shortfall_rationale: str | None = None,
    ) -> MonthlyBudgetPlanDraft:
        """Validate an agent-designed plan without selecting categories or amounts."""

        proposed = tuple(items)
        if not proposed:
            raise ValueError("A monthly budget plan must create at least one page")
        cap = money(financial_cap)
        if cap <= ZERO:
            raise ValueError("financial_cap must be positive")
        rationale = cap_rationale.strip()
        if not rationale:
            raise ValueError("cap_rationale cannot be empty")
        income = money(income_assumption) if income_assumption is not None else None
        if income is not None and income < ZERO:
            raise ValueError("income_assumption cannot be negative")
        _validate_cap_basis(context, cap_basis, cap, income)
        _validate_unique_subcategories(context, proposed)

        new_total = money(sum((item.amount for item in proposed), ZERO))
        total_after = money(context.existing_target_total + new_total)
        if total_after > cap:
            raise ValueError(
                f"Total Budget pages ₪{total_after} exceed financial cap ₪{cap}"
            )
        if income is not None and total_after > income:
            raise ValueError(
                "Total Budget pages exceed the stated income assumption and leave "
                "no valid variable reserve"
            )

        required_future = context.future_expense_allocation
        planned_future = money(
            sum(
                (
                    item.amount
                    for item in proposed
                    if item.purpose is BudgetPlanPurpose.FUTURE_EXPENSE
                ),
                ZERO,
            )
        )
        warnings: list[str] = []
        if context.future_expenses_status != "ready":
            warnings.append(
                "Future Expenses schema is unavailable; future needs rely on user-provided context."
            )
        elif planned_future < required_future:
            shortfall = money(required_future - planned_future)
            if (
                not future_expense_shortfall_rationale
                or not future_expense_shortfall_rationale.strip()
            ):
                raise ValueError(
                    f"Future-expense allocations are short by ₪{shortfall}; "
                    "provide an explicit rationale"
                )
            warnings.append(
                f"Future-expense allocation is ₪{shortfall} below the deterministic need: "
                f"{future_expense_shortfall_rationale.strip()}"
            )
        if income is None:
            warnings.append(
                "No income assumption was selected; variable reserve cannot be calculated."
            )

        previous_by_name = {
            budget.subcategory.casefold(): budget for budget in context.previous_budgets
        }
        stability = tuple(
            _stability(item, previous_by_name.get(item.subcategory.casefold()))
            for item in proposed
        )
        variable_reserve = money(income - total_after) if income is not None else None
        draft_without_id = {
            "target_month": context.target_month.isoformat(),
            "source_fingerprint": context.source_fingerprint,
            "cap_basis": cap_basis.value,
            "cap_rationale": rationale,
            "financial_cap": str(cap),
            "income_assumption": str(income) if income is not None else None,
            "items": _stable_value(proposed),
            "existing_target_budgets": _stable_value(
                context.existing_target_budgets
            ),
        }
        encoded = json.dumps(
            draft_without_id,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        operation_id = f"BCRT-{hashlib.sha256(encoded.encode()).hexdigest()[:24].upper()}"
        return MonthlyBudgetPlanDraft(
            operation_id=operation_id,
            target_month=context.target_month,
            source_fingerprint=context.source_fingerprint,
            cap_basis=cap_basis,
            cap_rationale=rationale,
            financial_cap=cap,
            income_assumption=income,
            existing_target_total=context.existing_target_total,
            new_budget_total=new_total,
            total_budget_after=total_after,
            unallocated_cap=money(cap - total_after),
            variable_reserve_after=variable_reserve,
            future_expense_allocation_required=required_future,
            future_expense_allocation_planned=planned_future,
            items=proposed,
            existing_target_budgets=context.existing_target_budgets,
            stability=stability,
            warnings=tuple(warnings),
        )

    async def apply_plan(
        self, draft: MonthlyBudgetPlanDraft
    ) -> BudgetPlanCreationResult:
        """Fresh-read the planning evidence and target pages before creating anything."""

        fresh = await self.planning_context(draft.target_month)
        if fresh.source_fingerprint != draft.source_fingerprint:
            raise BudgetPlanningFreshnessError(
                "Budget planning evidence changed after the draft was created"
            )
        baseline = {budget.id: budget for budget in draft.existing_target_budgets}
        requested = {item.subcategory.casefold(): item for item in draft.items}
        already_created: list[CreatedBudgetPage] = []
        seen_baseline: set[str] = set()
        for budget in fresh.existing_target_budgets:
            original = baseline.get(budget.id)
            if original is not None:
                if budget != original:
                    raise BudgetPlanningFreshnessError(
                        f"Existing budget {budget.subcategory!r} changed after drafting"
                    )
                seen_baseline.add(budget.id)
                continue
            planned = requested.get(budget.subcategory.casefold())
            if (
                planned is None
                or budget.last_adjustment_id != draft.operation_id
                or budget.amount != planned.amount
                or budget.progressive is not planned.progressive
                or budget.volatility_percent != planned.volatility_percent
            ):
                raise BudgetPlanningFreshnessError(
                    f"A conflicting budget page now exists for {budget.subcategory!r}"
                )
            already_created.append(
                CreatedBudgetPage(
                    page_id=budget.id,
                    subcategory=budget.subcategory,
                    amount=budget.amount,
                    already_created=True,
                )
            )
        if seen_baseline != set(baseline):
            raise BudgetPlanningFreshnessError(
                "An existing target-month budget page was removed after drafting"
            )
        return await self.repository.create(draft, tuple(already_created))


def _future_need(
    expense_id: str,
    name: str,
    due_date: date,
    target_amount: Decimal,
    saved_amount: Decimal,
    target_month: date,
) -> FutureExpenseBudgetNeed:
    allocation = planned_expense_monthly_allocation(
        PlannedExpense(expense_id, name, target_amount, due_date, saved_amount),
        target_month,
    )
    return FutureExpenseBudgetNeed(
        planned_expense_id=expense_id,
        name=name,
        due_date=due_date,
        remaining_amount=allocation.remaining_amount,
        monthly_allocation=allocation.monthly_allocation,
        status=allocation.status,
    )


def _validate_cap_basis(
    context: BudgetPlanningContext,
    basis: BudgetCapBasis,
    cap: Decimal,
    income: Decimal | None,
) -> None:
    if basis is BudgetCapBasis.TARGET_MONTH_INCOME:
        if context.target_month_income is None:
            raise ValueError("No target-month income exists for the selected cap basis")
        if income != context.target_month_income or cap > context.target_month_income:
            raise ValueError("Target-income cap basis must use the grounded target income")
    elif basis is BudgetCapBasis.PREVIOUS_MONTH_INCOME:
        if context.previous_month_income is None:
            raise ValueError("No previous-month income exists for the selected cap basis")
        if income != context.previous_month_income or cap > context.previous_month_income:
            raise ValueError("Previous-income cap basis must use the grounded previous income")
    elif basis is BudgetCapBasis.PREVIOUS_MONTH_BUDGETS:
        if not context.previous_budgets:
            raise ValueError("No previous-month budgets exist for the selected cap basis")
        if cap > context.previous_budget_total:
            raise ValueError(
                "A cap above previous budgeting must use mixed or user-provided evidence"
            )


def _validate_unique_subcategories(
    context: BudgetPlanningContext,
    items: tuple[BudgetPageDraft, ...],
) -> None:
    existing = {
        budget.subcategory.strip().casefold()
        for budget in context.existing_target_budgets
    }
    seen: set[str] = set()
    for item in items:
        key = item.subcategory.casefold()
        if key in existing:
            raise ValueError(
                f"A target-month Budget page already exists for {item.subcategory!r}"
            )
        if key in seen:
            raise ValueError(f"Duplicate proposed subcategory: {item.subcategory!r}")
        seen.add(key)


def _stability(
    item: BudgetPageDraft, previous: Budget | None
) -> BudgetStabilityComparison:
    return BudgetStabilityComparison(
        subcategory=item.subcategory,
        previous_amount=previous.amount if previous is not None else None,
        proposed_amount=item.amount,
        delta=(money(item.amount - previous.amount) if previous is not None else None),
    )


def _source_fingerprint(
    previous_budgets: tuple[Budget, ...],
    target_income: Decimal | None,
    previous_income: Decimal | None,
    future_expenses: tuple[FutureExpenseBudgetNeed, ...],
    future_status: str,
    guidelines: tuple[BudgetPlanningGuideline, ...],
) -> str:
    payload = json.dumps(
        _stable_value(
            {
                "previous_budgets": previous_budgets,
                "target_income": target_income,
                "previous_income": previous_income,
                "future_expenses": future_expenses,
                "future_status": future_status,
                "guidelines": guidelines,
            }
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _stable_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _stable_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _stable_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_stable_value(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    raise TypeError(f"Unsupported budget fingerprint value: {type(value).__name__}")


def _income_total(incomes: Sequence[Income]) -> Decimal | None:
    if not incomes:
        return None
    return money(sum((item.amount for item in incomes), ZERO))


def _budget_scope(scope: str) -> bool:
    return scope.strip().casefold() in {
        "budgeting",
        "budget planning",
        "savings",
        "future expenses",
    }


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _month_end(value: date) -> date:
    return date(
        value.year,
        value.month,
        calendar.monthrange(value.year, value.month)[1],
    )
