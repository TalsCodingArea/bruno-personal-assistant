"""Read-only account outlook assembled from authoritative finance sources."""

import json
from datetime import date
from decimal import Decimal, InvalidOperation

from financial_agent.domain.cashflow import AccountOutlook
from financial_agent.domain.money import ZERO, money
from financial_agent.services.finance_queries import month_bounds
from financial_agent.services.ports import BankMovementReader, FinanceReader
from financial_agent.services.profile import FinancialProfileService

MINIMUM_BALANCE_KEY = "cashflow.minimum_available_balance"
SAVINGS_SWEEP_KEY = "cashflow.savings_sweep_surplus"


def _next_month(month: date) -> date:
    return date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)


def _rule_amount(statement: str, key: str) -> Decimal:
    try:
        parsed = json.loads(statement)
        if isinstance(parsed, bool) or not isinstance(parsed, int | float | str):
            raise ValueError
        value = money(Decimal(str(parsed)))
    except (json.JSONDecodeError, InvalidOperation, ValueError) as exc:
        raise ValueError(f"{key} must contain one non-negative JSON number") from exc
    if value < ZERO:
        raise ValueError(f"{key} cannot be negative")
    return value


class CashflowService:
    """Calculate a transparent start-of-month projection without any writes."""

    def __init__(
        self,
        finance: FinanceReader,
        bank: BankMovementReader,
        profile: FinancialProfileService,
    ) -> None:
        self.finance = finance
        self.bank = bank
        self.profile = profile

    async def bank_movements(
        self, start_date: date, end_date: date, *, limit: int = 50
    ) -> tuple[object, ...]:
        if end_date < start_date:
            raise ValueError("end_date cannot be before start_date")
        return await self.bank.movements(start_date, end_date, limit=limit)

    async def account_outlook(self, expense_month: date, as_of: date) -> AccountOutlook:
        expense_month = expense_month.replace(day=1)
        settlement_month = _next_month(expense_month)
        latest = await self.bank.latest(as_of)
        settlement = await self.finance.expense_settlement(expense_month)

        target_start, target_end = month_bounds(settlement_month)
        incomes = await self.finance.incomes(target_start, target_end)
        target_budgets = await self.finance.budgets(settlement_month)
        salary_basis = "recorded settlement-month income"
        rent_basis = "settlement-month Rent budget"
        if not incomes:
            source_start, source_end = month_bounds(expense_month)
            incomes = await self.finance.incomes(source_start, source_end)
            salary_basis = "previous month recorded income"
        rent = next(
            (
                item.amount
                for item in target_budgets
                if item.subcategory.strip().casefold() == "rent"
            ),
            None,
        )
        if rent is None:
            source_budgets = await self.finance.budgets(expense_month)
            rent = next(
                (
                    item.amount
                    for item in source_budgets
                    if item.subcategory.strip().casefold() == "rent"
                ),
                ZERO,
            )
            rent_basis = "previous month Rent budget"
        salary = money(sum((item.amount for item in incomes), ZERO))
        rent = money(rent)

        entries = await self.profile.active_entries(limit=100)
        rules = {entry.key: entry.statement for entry in entries}
        minimum = (
            _rule_amount(rules[MINIMUM_BALANCE_KEY], MINIMUM_BALANCE_KEY)
            if MINIMUM_BALANCE_KEY in rules
            else None
        )
        sweep = (
            _rule_amount(rules[SAVINGS_SWEEP_KEY], SAVINGS_SWEEP_KEY)
            if SAVINGS_SWEEP_KEY in rules
            else None
        )
        projected = (
            money(
                latest.balance_after
                + salary
                - rent
                - settlement.credit_charges
                + settlement.expected_reimbursement
            )
            if latest is not None
            else None
        )
        above = (
            money(projected - minimum)
            if projected is not None and minimum is not None
            else None
        )
        transfer = (
            above
            if above is not None and sweep is not None and above >= sweep
            else ZERO
            if above is not None and sweep is not None
            else None
        )
        missing = tuple(
            key
            for key, value in ((MINIMUM_BALANCE_KEY, minimum), (SAVINGS_SWEEP_KEY, sweep))
            if value is None
        )
        return AccountOutlook(
            as_of=as_of,
            expense_month=expense_month,
            settlement_month=settlement_month,
            latest_movement=latest,
            expense_settlement=settlement,
            expected_salary=salary,
            expected_salary_basis=salary_basis,
            expected_rent=rent,
            expected_rent_basis=rent_basis,
            projected_balance_after_settlement=projected,
            minimum_available_balance=minimum,
            available_above_minimum=above,
            on_track=above >= ZERO if above is not None else None,
            savings_sweep_threshold=sweep,
            recommended_savings_transfer=transfer,
            missing_rules=missing,
        )
