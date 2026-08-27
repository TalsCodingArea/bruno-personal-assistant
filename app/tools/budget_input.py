"""Shared schemas and conversion for monthly budget planning tools."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.budget_planning import (
    BudgetCapBasis,
    BudgetPageDraft,
    BudgetPlanPurpose,
    MonthlyBudgetPlanDraft,
)
from app.domain.models import ProgressiveMode
from app.services.budget_planning import BudgetPlanningService
from app.tools.money_input import CurrencyText, parse_currency_text


class BudgetPageDraftInput(BaseModel):
    subcategory: str = Field(min_length=1, max_length=200)
    amount: CurrencyText
    progressive: Literal["Accumulated", "Discrete"]
    volatility_percent: CurrencyText = Field(
        description="Controllable share from 0 through 100 as a base-10 string."
    )
    purpose: Literal["regular", "future_expense"] = "regular"
    rationale: str = Field(min_length=1, max_length=1000)


class MonthlyBudgetPlanInput(BaseModel):
    month: str = Field(
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="Target month in YYYY-MM format.",
    )
    source_fingerprint: str = Field(
        pattern=r"^[a-f0-9]{64}$",
        description="Exact fingerprint returned by get_budget_planning_context.",
    )
    financial_cap: CurrencyText = Field(
        description="Maximum total Budget pages after this plan."
    )
    cap_basis: Literal[
        "user_provided",
        "target_month_income",
        "previous_month_income",
        "previous_month_budgets",
        "mixed",
    ]
    cap_rationale: str = Field(min_length=1, max_length=1800)
    income_assumption: CurrencyText | None = Field(
        default=None,
        description="Grounded income used to calculate the remaining variable reserve.",
    )
    future_expense_shortfall_rationale: str | None = Field(
        default=None,
        max_length=1800,
        description="Required only when intentionally funding less than future-expense need.",
    )
    items: tuple[BudgetPageDraftInput, ...] = Field(min_length=1, max_length=100)


async def prepare_budget_plan(
    service: BudgetPlanningService,
    *,
    month: str,
    source_fingerprint: str,
    financial_cap: str,
    cap_basis: str,
    cap_rationale: str,
    items: tuple[BudgetPageDraftInput, ...],
    income_assumption: str | None = None,
    future_expense_shortfall_rationale: str | None = None,
) -> MonthlyBudgetPlanDraft:
    context = await service.planning_context(
        date.fromisoformat(f"{month}-01")
    )
    if context.source_fingerprint != source_fingerprint:
        raise ValueError(
            "Budget planning context changed; call get_budget_planning_context again"
        )
    domain_items = tuple(
        BudgetPageDraft(
            subcategory=item.subcategory,
            amount=parse_currency_text(
                item.amount,
                field_name=f"amount[{item.subcategory}]",
                positive=True,
            ),
            progressive=ProgressiveMode(item.progressive),
            volatility_percent=parse_currency_text(
                item.volatility_percent,
                field_name=f"volatility_percent[{item.subcategory}]",
                non_negative=True,
            ),
            purpose=BudgetPlanPurpose(item.purpose),
            rationale=item.rationale,
        )
        for item in items
    )
    return service.draft_plan(
        context,
        domain_items,
        financial_cap=parse_currency_text(
            financial_cap,
            field_name="financial_cap",
            positive=True,
        ),
        cap_basis=BudgetCapBasis(cap_basis),
        cap_rationale=cap_rationale,
        income_assumption=(
            parse_currency_text(
                income_assumption,
                field_name="income_assumption",
                non_negative=True,
            )
            if income_assumption is not None
            else None
        ),
        future_expense_shortfall_rationale=future_expense_shortfall_rationale,
    )
