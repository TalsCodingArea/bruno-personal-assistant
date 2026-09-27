"""Pure, deterministic finance calculations.

Functions in this module perform no I/O and know nothing about Notion, agents, or graphs.
"""

import calendar
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from financial_agent.domain.models import (
    Budget,
    CategorySuggestion,
    CategoryUpdateDraft,
    PlannedExpense,
    PlannedExpenseDraft,
    ProgressiveMode,
    Transaction,
)
from financial_agent.domain.money import CENT, ZERO, money


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    category: str
    subcategory: str
    amount: Decimal
    transaction_count: int
    expense_class: str | None = None


@dataclass(frozen=True, slots=True)
class MonthlyCategorySummary:
    month: date
    total: Decimal
    categories: tuple[CategoryTotal, ...]


@dataclass(frozen=True, slots=True)
class VariableSpendingPool:
    expected_income: Decimal
    regular_expense_budgets: Decimal
    savings: Decimal
    buffer: Decimal
    available: Decimal
    shortfall: Decimal


@dataclass(frozen=True, slots=True)
class PlannedExpenseAllocation:
    planned_expense_id: str
    remaining_amount: Decimal
    monthly_allocation: Decimal
    contribution_months: int
    saving_starts_on: date
    status: str


@dataclass(frozen=True, slots=True)
class MonthEndForecast:
    month: date
    as_of: date
    spent_to_date: Decimal
    elapsed_days: int
    days_in_month: int
    regular_spent_to_date: Decimal
    variable_spent_to_date: Decimal
    accumulated_regular_projection: Decimal
    discrete_regular_projection: Decimal
    variable_projection: Decimal
    projected_expenses: Decimal
    expected_income: Decimal
    planned_allocations: Decimal
    buffer: Decimal
    projected_available: Decimal
    assumptions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CategoryBudgetStatus:
    subcategory: str
    budget: Decimal
    spent: Decimal
    remaining: Decimal
    expense_class: str
    progressive: ProgressiveMode | None
    volatility_percent: Decimal | None
    controllable_remaining: Decimal | None
    percent_used: Decimal | None
    over_budget: bool


@dataclass(frozen=True, slots=True)
class MonthlyBudgetStatus:
    month: date
    total_budget: Decimal
    total_spent: Decimal
    regular_spend: Decimal
    variable_spend: Decimal
    remaining: Decimal
    categories: tuple[CategoryBudgetStatus, ...]
    variable_categories: tuple[CategoryTotal, ...]


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _same_month(left: date, right: date) -> bool:
    return left.year == right.year and left.month == right.month


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _month_difference(later: date, earlier: date) -> int:
    return (later.year - earlier.year) * 12 + later.month - earlier.month


def _classification_label(canonical: str | None, options: tuple[str, ...]) -> str:
    if canonical is not None:
        return canonical
    if len(options) > 1:
        return f"Multiple: {' | '.join(options)}"
    return "Uncategorized"


def monthly_category_summary(
    transactions: tuple[Transaction, ...] | list[Transaction],
    month: date,
    *,
    budgets: tuple[Budget, ...] | list[Budget] | None = None,
) -> MonthlyCategorySummary:
    """Sum one month's transactions by exact category and sub-category."""

    regular_subcategories = (
        {budget.subcategory for budget in budgets if budget.month == _month_start(month)}
        if budgets is not None
        else None
    )

    def expense_class(subcategory: str) -> str | None:
        if regular_subcategories is None:
            return None
        return "regular" if subcategory in regular_subcategories else "variable"

    grouped: defaultdict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    counts: Counter[tuple[str, str]] = Counter()
    for transaction in transactions:
        if not _same_month(transaction.occurred_on, month):
            continue
        key = (
            _classification_label(transaction.category, transaction.category_options),
            _classification_label(
                transaction.subcategory, transaction.subcategory_options
            ),
        )
        grouped[key] += transaction.final_amount
        counts[key] += 1
    rows = tuple(
        CategoryTotal(
            category=key[0],
            subcategory=key[1],
            amount=money(amount),
            transaction_count=counts[key],
            expense_class=expense_class(key[1]),
        )
        for key, amount in sorted(grouped.items(), key=lambda item: (-item[1], item[0]))
    )
    return MonthlyCategorySummary(
        month=_month_start(month),
        total=money(sum((row.amount for row in rows), ZERO)),
        categories=rows,
    )


def uncategorized_review(
    transactions: tuple[Transaction, ...] | list[Transaction],
) -> tuple[Transaction, ...]:
    """Return transactions missing either category level."""

    empty_labels = {"", "uncategorized", "unassigned", "unknown"}

    def missing(value: str | None) -> bool:
        return value is None or value.strip().casefold() in empty_labels

    return tuple(
        sorted(
            (
                transaction
                for transaction in transactions
                if missing(transaction.category) or missing(transaction.subcategory)
            ),
            key=lambda transaction: (transaction.occurred_on, transaction.id),
            reverse=True,
        )
    )


def variable_spending_pool(
    expected_income: Decimal,
    regular_expense_budgets: Decimal,
    savings: Decimal,
    buffer: Decimal,
) -> VariableSpendingPool:
    """Calculate money available after non-variable priorities."""

    values = tuple(
        money(value)
        for value in (expected_income, regular_expense_budgets, savings, buffer)
    )
    if any(value < ZERO for value in values):
        raise ValueError("variable spending inputs cannot be negative")
    income, fixed, saving, safety_buffer = values
    raw_available = money(income - fixed - saving - safety_buffer)
    return VariableSpendingPool(
        expected_income=income,
        regular_expense_budgets=fixed,
        savings=saving,
        buffer=safety_buffer,
        available=max(raw_available, ZERO),
        shortfall=max(-raw_available, ZERO),
    )


def planned_expense_monthly_allocation(
    planned_expense: PlannedExpense, today: date
) -> PlannedExpenseAllocation:
    """Allocate the remaining cost across no more than three contribution months."""

    remaining = money(max(planned_expense.target_amount - planned_expense.saved_amount, ZERO))
    due_month = _month_start(planned_expense.due_date)
    current_month = _month_start(today)
    canonical_start = _add_months(due_month, -2)

    if remaining == ZERO:
        return PlannedExpenseAllocation(
            planned_expense_id=planned_expense.id,
            remaining_amount=ZERO,
            monthly_allocation=ZERO,
            contribution_months=0,
            saving_starts_on=max(canonical_start, current_month),
            status="funded",
        )
    if planned_expense.due_date < today:
        contribution_months = 1
        saving_start = current_month
        status = "overdue"
    elif current_month < canonical_start:
        contribution_months = 3
        saving_start = canonical_start
        status = "scheduled"
    else:
        contribution_months = min(3, _month_difference(due_month, current_month) + 1)
        contribution_months = max(contribution_months, 1)
        saving_start = current_month
        status = "saving"
    allocation = (remaining / Decimal(contribution_months)).quantize(
        CENT, rounding=ROUND_HALF_UP
    )
    return PlannedExpenseAllocation(
        planned_expense_id=planned_expense.id,
        remaining_amount=remaining,
        monthly_allocation=allocation,
        contribution_months=contribution_months,
        saving_starts_on=saving_start,
        status=status,
    )


def month_end_forecast(
    transactions: tuple[Transaction, ...] | list[Transaction],
    budgets: tuple[Budget, ...] | list[Budget],
    month: date,
    as_of: date,
    expected_income: Decimal,
    *,
    planned_allocations: Decimal = ZERO,
    buffer: Decimal = ZERO,
) -> MonthEndForecast:
    """Forecast regular and variable expenses according to their budget behavior."""

    month = _month_start(month)
    if not _same_month(month, as_of):
        raise ValueError("as_of must be inside the forecast month")
    last_day = calendar.monthrange(month.year, month.month)[1]
    monthly_budgets = {budget.subcategory: budget for budget in budgets if budget.month == month}
    if len(monthly_budgets) != sum(budget.month == month for budget in budgets):
        raise ValueError(f"Duplicate budget sub-category in {month:%Y-%m}")
    regular_spending: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
    variable_spent = ZERO
    for transaction in transactions:
        if not _same_month(transaction.occurred_on, month) or transaction.occurred_on > as_of:
            continue
        if transaction.subcategory and transaction.subcategory in monthly_budgets:
            regular_spending[transaction.subcategory] += transaction.final_amount
        else:
            variable_spent += transaction.final_amount

    def pace(amount: Decimal) -> Decimal:
        return (amount / Decimal(as_of.day) * Decimal(last_day)).quantize(
            CENT, rounding=ROUND_HALF_UP
        )

    accumulated_projection = ZERO
    discrete_projection = ZERO
    assumptions: list[str] = []
    for subcategory, budget in monthly_budgets.items():
        spent = money(regular_spending[subcategory])
        if budget.progressive is ProgressiveMode.ACCUMULATED:
            accumulated_projection += pace(spent)
        else:
            discrete_projection += max(spent, budget.amount)
            if budget.progressive is None:
                assumptions.append(
                    f"{subcategory}: missing Progressive; conservatively reserved full budget."
                )
    regular_spent = money(sum(regular_spending.values(), ZERO))
    variable_spent = money(variable_spent)
    accumulated_projection = money(accumulated_projection)
    discrete_projection = money(discrete_projection)
    variable_projection = pace(variable_spent)
    projected = money(accumulated_projection + discrete_projection + variable_projection)
    spent = money(regular_spent + variable_spent)
    income = money(expected_income)
    allocations = money(planned_allocations)
    safety_buffer = money(buffer)
    return MonthEndForecast(
        month=month,
        as_of=as_of,
        spent_to_date=spent,
        elapsed_days=as_of.day,
        days_in_month=last_day,
        regular_spent_to_date=regular_spent,
        variable_spent_to_date=variable_spent,
        accumulated_regular_projection=accumulated_projection,
        discrete_regular_projection=discrete_projection,
        variable_projection=variable_projection,
        projected_expenses=projected,
        expected_income=income,
        planned_allocations=allocations,
        buffer=safety_buffer,
        projected_available=money(income - projected - allocations - safety_buffer),
        assumptions=tuple(assumptions),
    )


def budget_status(
    transactions: tuple[Transaction, ...] | list[Transaction],
    budgets: tuple[Budget, ...] | list[Budget],
    month: date,
) -> MonthlyBudgetStatus:
    """Compare exact sub-category budgets with actual Final spending."""

    month = _month_start(month)
    spending: defaultdict[str, Decimal] = defaultdict(lambda: ZERO)
    spending_counts: Counter[str] = Counter()
    for transaction in transactions:
        if _same_month(transaction.occurred_on, month):
            subcategory = _classification_label(
                transaction.subcategory, transaction.subcategory_options
            )
            spending[subcategory] += transaction.final_amount
            spending_counts[subcategory] += 1
    rows: list[CategoryBudgetStatus] = []
    seen_subcategories: set[str] = set()
    total_budget = ZERO
    total_budgeted_spend = ZERO
    for budget in budgets:
        if budget.month != month:
            continue
        if budget.subcategory in seen_subcategories:
            raise ValueError(
                f"Duplicate budget for exact sub-category {budget.subcategory!r} in {month:%Y-%m}"
            )
        seen_subcategories.add(budget.subcategory)
        spent = money(spending.pop(budget.subcategory, ZERO))
        total_budget += budget.amount
        total_budgeted_spend += spent
        percent = (
            (spent / budget.amount * Decimal(100)).quantize(Decimal("0.1"))
            if budget.amount > ZERO
            else None
        )
        remaining = money(budget.amount - spent)
        controllable = (
            money(max(remaining, ZERO) * budget.volatility_percent / Decimal(100))
            if budget.volatility_percent is not None
            else None
        )
        rows.append(
            CategoryBudgetStatus(
                subcategory=budget.subcategory,
                budget=budget.amount,
                spent=spent,
                remaining=remaining,
                expense_class="regular",
                progressive=budget.progressive,
                volatility_percent=budget.volatility_percent,
                controllable_remaining=controllable,
                percent_used=percent,
                over_budget=spent > budget.amount,
            )
        )
    rows.sort(key=lambda row: (row.remaining, row.subcategory))
    variable_rows = tuple(
        CategoryTotal(
            category="Variable",
            subcategory=subcategory,
            amount=money(amount),
            transaction_count=spending_counts[subcategory],
            expense_class="variable",
        )
        for subcategory, amount in sorted(spending.items(), key=lambda item: (-item[1], item[0]))
    )
    variable_spend = money(sum(spending.values(), ZERO))
    regular_spend = money(total_budgeted_spend)
    return MonthlyBudgetStatus(
        month=month,
        total_budget=money(total_budget),
        total_spent=money(regular_spend + variable_spend),
        regular_spend=regular_spend,
        variable_spend=variable_spend,
        remaining=money(total_budget - total_budgeted_spend),
        categories=tuple(rows),
        variable_categories=variable_rows,
    )


def _normalized_merchant(description: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", description.casefold()))


def suggest_categories(
    transactions: tuple[Transaction, ...] | list[Transaction],
    categorized_history: tuple[Transaction, ...] | list[Transaction],
) -> tuple[CategorySuggestion, ...]:
    """Suggest from exact normalized merchant history; never ask an LLM to guess."""

    history: defaultdict[str, Counter[tuple[str, str]]] = defaultdict(Counter)
    for transaction in categorized_history:
        if transaction.category and transaction.subcategory:
            history[_normalized_merchant(transaction.description)][
                (transaction.category, transaction.subcategory)
            ] += 1
    suggestions: list[CategorySuggestion] = []
    for transaction in uncategorized_review(transactions):
        merchant = _normalized_merchant(transaction.description)
        matches = history.get(merchant)
        if not matches:
            continue
        ranked = matches.most_common()
        (category, subcategory), count = ranked[0]
        total_matches = sum(matches.values())
        confidence = (Decimal(count) / Decimal(total_matches)).quantize(Decimal("0.01"))
        suggestions.append(
            CategorySuggestion(
                transaction_id=transaction.id,
                category=category,
                subcategory=subcategory,
                confidence=confidence,
                reason=f"Matched {count} of {total_matches} prior transactions for this merchant.",
            )
        )
    return tuple(suggestions)


def draft_category_updates(
    suggestions: tuple[CategorySuggestion, ...] | list[CategorySuggestion],
) -> tuple[CategoryUpdateDraft, ...]:
    """Turn grounded suggestions into non-mutating proposals."""

    return tuple(
        CategoryUpdateDraft(
            transaction_id=item.transaction_id,
            category=item.category,
            subcategory=item.subcategory,
            reason=item.reason,
        )
        for item in suggestions
    )


def draft_planned_expense(
    name: str,
    target_amount: Decimal,
    due_date: date,
    today: date,
    *,
    saved_amount: Decimal = ZERO,
) -> PlannedExpenseDraft:
    """Validate and calculate a proposed planned expense without persisting it."""

    planned = PlannedExpense(
        id="draft",
        name=name.strip(),
        target_amount=target_amount,
        due_date=due_date,
        saved_amount=saved_amount,
    )
    if not planned.name:
        raise ValueError("name cannot be empty")
    allocation = planned_expense_monthly_allocation(planned, today)
    return PlannedExpenseDraft(
        name=planned.name,
        target_amount=planned.target_amount,
        due_date=planned.due_date,
        saved_amount=planned.saved_amount,
        monthly_allocation=allocation.monthly_allocation,
        saving_starts_on=allocation.saving_starts_on,
    )
