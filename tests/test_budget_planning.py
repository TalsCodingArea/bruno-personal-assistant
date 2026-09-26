"""Grounded context and deterministic validation for agent-directed budget plans."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from financial_agent.domain.budget_planning import (
    BudgetCapBasis,
    BudgetPageDraft,
    BudgetPlanCreationResult,
    BudgetPlanningFreshnessError,
    BudgetPlanPurpose,
    CreatedBudgetPage,
    ExistingTargetBudgetPagesError,
)
from financial_agent.domain.models import Budget, Income, PlannedExpense, ProgressiveMode
from financial_agent.domain.profile import FinancialProfileEntry, ProfileKind, ProfileStatus
from financial_agent.services.budget_planning import BudgetPlanningService
from financial_agent.tools.draft.budget import build_budget_draft_tools
from financial_agent.tools.read.budget import build_budget_read_tools
from financial_agent.tools.write.budget import build_budget_write_tools

from tests.fakes import FakeFinanceReader

TARGET = date(2026, 9, 1)
PREVIOUS = date(2026, 8, 1)
EDITED = datetime(2026, 8, 27, 10, tzinfo=UTC)


class Rules:
    def __init__(self, entries: tuple[FinancialProfileEntry, ...] = ()) -> None:
        self.entries = entries

    async def active_entries(
        self, *, limit: int = 100
    ) -> tuple[FinancialProfileEntry, ...]:
        return self.entries[:limit]


class CreationRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[object, tuple[CreatedBudgetPage, ...]]] = []

    async def create(self, draft, already_created):  # type: ignore[no-untyped-def]
        self.calls.append((draft, already_created))
        pages = list(already_created)
        existing = {item.subcategory.casefold() for item in already_created}
        pages.extend(
            CreatedBudgetPage(
                page_id=f"created-{item.subcategory}",
                subcategory=item.subcategory,
                amount=item.amount,
                already_created=False,
            )
            for item in draft.items
            if item.subcategory.casefold() not in existing
        )
        return BudgetPlanCreationResult(draft.operation_id, tuple(pages))


class UnconfiguredFutureExpensesReader(FakeFinanceReader):
    async def planned_expenses(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError("Future Expenses schema is unavailable")


def budget(
    page_id: str,
    subcategory: str,
    amount: str,
    month: date = PREVIOUS,
    *,
    operation_id: str | None = None,
) -> Budget:
    return Budget(
        page_id,
        subcategory,
        month,
        Decimal(amount),
        ProgressiveMode.ACCUMULATED,
        Decimal("50"),
        baseline_amount=Decimal(amount),
        last_adjustment_id=operation_id,
        last_edited_at=EDITED,
    )


def planning_rule() -> FinancialProfileEntry:
    return FinancialProfileEntry(
        page_id="rule-stability",
        name="Stable budgets",
        key="budgeting.prefer_stability",
        kind=ProfileKind.PREFERENCE,
        scopes=("Budget Planning",),
        statement="Keep regular allocations stable unless evidence changes.",
        status=ProfileStatus.ACTIVE,
        operation_id="CTX-9",
        last_edited_at=EDITED,
    )


def service_and_finance() -> tuple[BudgetPlanningService, FakeFinanceReader, CreationRepository]:
    finance = FakeFinanceReader(
        incomes=(
            Income("aug-income", "Salary", PREVIOUS, Decimal("2000")),
            Income("sep-income", "Salary", TARGET, Decimal("2200")),
        ),
        budgets=(budget("aug-rent", "Rent", "1000"),),
        planned=(
            PlannedExpense(
                "future-laptop",
                "Laptop",
                Decimal("900"),
                date(2026, 11, 15),
            ),
        ),
    )
    repository = CreationRepository()
    return (
        BudgetPlanningService(finance, Rules((planning_rule(),)), repository),
        finance,
        repository,
    )


def plan_items() -> tuple[BudgetPageDraft, ...]:
    return (
        BudgetPageDraft(
            "Rent",
            Decimal("1000"),
            ProgressiveMode.DISCRETE,
            Decimal("0"),
            BudgetPlanPurpose.REGULAR,
            "Protected rent paid as one monthly charge.",
        ),
        BudgetPageDraft(
            "Laptop reserve",
            Decimal("300"),
            ProgressiveMode.DISCRETE,
            Decimal("0"),
            BudgetPlanPurpose.FUTURE_EXPENSE,
            "One of three contributions toward the November laptop.",
        ),
    )


def test_context_loads_prior_budget_income_future_allocations_and_rules() -> None:
    service, _, _ = service_and_finance()

    context = asyncio.run(service.planning_context(TARGET))

    assert context.previous_budget_total == Decimal("1000.00")
    assert context.target_month_income == Decimal("2200.00")
    assert context.previous_month_income == Decimal("2000.00")
    assert context.future_expense_allocation == Decimal("300.00")
    assert context.future_expenses[0].status == "saving"
    assert context.guidelines[0].key == "budgeting.prefer_stability"
    assert len(context.source_fingerprint) == 64


def test_future_expense_budget_must_be_discrete() -> None:
    with pytest.raises(
        ValueError,
        match="Future-expense Budget pages must use Discrete Progressive",
    ):
        BudgetPageDraft(
            subcategory="Laptop reserve",
            amount=Decimal("300"),
            progressive=ProgressiveMode.ACCUMULATED,
            volatility_percent=Decimal("0"),
            purpose=BudgetPlanPurpose.FUTURE_EXPENSE,
            rationale="Monthly contribution toward a future laptop.",
        )


def test_context_surfaces_unconfigured_future_expenses_without_inventing_none() -> None:
    service = BudgetPlanningService(
        UnconfiguredFutureExpensesReader(),
        Rules(),
        CreationRepository(),
    )

    context = asyncio.run(service.planning_context(TARGET))
    draft = service.draft_plan(
        context,
        (plan_items()[0],),
        financial_cap=Decimal("1500"),
        cap_basis=BudgetCapBasis.USER_PROVIDED,
        cap_rationale="Tal supplied the cap.",
    )

    assert context.future_expenses_status == "schema_not_configured"
    assert context.future_expenses == ()
    assert any("Future Expenses schema" in warning for warning in draft.warnings)


def test_agent_selected_plan_reports_stability_variable_reserve_and_future_coverage() -> None:
    service, _, _ = service_and_finance()
    context = asyncio.run(service.planning_context(TARGET))

    draft = service.draft_plan(
        context,
        plan_items(),
        financial_cap=Decimal("1500"),
        cap_basis=BudgetCapBasis.USER_PROVIDED,
        cap_rationale="Tal set a ₪1,500 cap.",
        income_assumption=Decimal("2200"),
    )

    assert draft.total_budget_after == Decimal("1300.00")
    assert draft.unallocated_cap == Decimal("200.00")
    assert draft.variable_reserve_after == Decimal("900.00")
    assert draft.future_expense_allocation_required == Decimal("300.00")
    assert draft.future_expense_allocation_planned == Decimal("300.00")
    rent_stability = draft.stability[0]
    assert rent_stability.previous_amount == Decimal("1000.00")
    assert rent_stability.proposed_amount == Decimal("1000.00")
    assert rent_stability.delta == Decimal("0.00")
    assert rent_stability.previous_progressive is ProgressiveMode.ACCUMULATED
    assert rent_stability.proposed_progressive is ProgressiveMode.DISCRETE
    assert rent_stability.previous_volatility_percent == Decimal("50.00")
    assert rent_stability.proposed_volatility_percent == Decimal("0.00")

    future_stability = draft.stability[1]
    assert future_stability.previous_amount is None
    assert future_stability.previous_progressive is None
    assert future_stability.previous_volatility_percent is None
    assert future_stability.proposed_progressive is ProgressiveMode.DISCRETE
    assert future_stability.proposed_volatility_percent == Decimal("0.00")
    assert draft.operation_id.startswith("BCRT-")


def test_previous_budget_basis_cannot_silently_expand_the_cap() -> None:
    service, _, _ = service_and_finance()
    context = asyncio.run(service.planning_context(TARGET))

    with pytest.raises(ValueError, match="mixed or user-provided"):
        service.draft_plan(
            context,
            (plan_items()[0],),
            financial_cap=Decimal("1200"),
            cap_basis=BudgetCapBasis.PREVIOUS_MONTH_BUDGETS,
            cap_rationale="Carry forward last month.",
        )


def test_plan_rejects_cap_overrun_duplicate_pages_and_silent_future_shortfall() -> None:
    service, finance, _ = service_and_finance()
    context = asyncio.run(service.planning_context(TARGET))

    with pytest.raises(ValueError, match="exceed financial cap"):
        service.draft_plan(
            context,
            plan_items(),
            financial_cap=Decimal("1200"),
            cap_basis=BudgetCapBasis.USER_PROVIDED,
            cap_rationale="Too small.",
        )
    with pytest.raises(ValueError, match="Future-expense allocations are short"):
        service.draft_plan(
            context,
            (plan_items()[0],),
            financial_cap=Decimal("1500"),
            cap_basis=BudgetCapBasis.USER_PROVIDED,
            cap_rationale="Missing future funding.",
        )

    finance.budget_rows = (
        *finance.budget_rows,
        budget("sep-rent", "Rent", "1000", TARGET),
    )
    changed_context = asyncio.run(service.planning_context(TARGET))
    with pytest.raises(
        ExistingTargetBudgetPagesError, match="already exists"
    ) as caught:
        service.draft_plan(
            changed_context,
            (plan_items()[0],),
            financial_cap=Decimal("1500"),
            cap_basis=BudgetCapBasis.USER_PROVIDED,
            cap_rationale="Duplicate target page.",
        )
    assert caught.value.subcategories == ("Rent",)


@pytest.mark.parametrize(
    "tool_factory",
    (build_budget_draft_tools, build_budget_write_tools),
)
def test_budget_tools_return_existing_pages_as_agent_actionable_result(
    tool_factory,
) -> None:  # type: ignore[no-untyped-def]
    service, finance, repository = service_and_finance()
    finance.budget_rows = (
        *finance.budget_rows,
        budget("sep-bills", "Bills 🧾", "200", TARGET),
    )
    context = asyncio.run(service.planning_context(TARGET))
    (tool,) = tool_factory(service)

    result = asyncio.run(
        tool.ainvoke(
            {
                "month": "2026-09",
                "source_fingerprint": context.source_fingerprint,
                "financial_cap": "2200",
                "cap_basis": "user_provided",
                "cap_rationale": "Use the current income as the cap.",
                "income_assumption": "2200",
                "items": [
                    {
                        "subcategory": "Bills 🧾",
                        "amount": "200",
                        "progressive": "Discrete",
                        "volatility_percent": "0",
                        "purpose": "regular",
                        "rationale": "Monthly bills reserve.",
                    }
                ],
            }
        )
    )

    assert result["status"] == "already_exists"
    assert result["existing_subcategories"] == ["Bills 🧾"]
    assert result["recovery"] == {
        "tool": "get_budget_planning_context",
        "arguments": {"month": "2026-09"},
        "current_pages_field": "existing_target_budgets",
    }
    assert "Do not recreate" in result["message"]
    assert "existing_target_budgets as the current pages" in result["next_action"]
    assert "only subcategories that do not yet have a page" in result["next_action"]
    (context_tool,) = build_budget_read_tools(service)
    refreshed = asyncio.run(context_tool.ainvoke({"month": "2026-09"}))
    assert [page["subcategory"] for page in refreshed["existing_target_budgets"]] == [
        "Bills 🧾"
    ]
    assert repository.calls == []


def test_apply_rechecks_sources_and_recognizes_idempotently_created_pages() -> None:
    service, finance, repository = service_and_finance()
    context = asyncio.run(service.planning_context(TARGET))
    draft = service.draft_plan(
        context,
        plan_items(),
        financial_cap=Decimal("1500"),
        cap_basis=BudgetCapBasis.USER_PROVIDED,
        cap_rationale="Tal set the cap.",
        income_assumption=Decimal("2200"),
    )
    first = draft.items[0]
    finance.budget_rows = (
        *finance.budget_rows,
        Budget(
            "created-rent",
            first.subcategory,
            TARGET,
            first.amount,
            first.progressive,
            first.volatility_percent,
            baseline_amount=first.amount,
            last_adjustment_id=draft.operation_id,
            last_edited_at=EDITED,
        ),
    )

    result = asyncio.run(service.apply_plan(draft))

    already = repository.calls[0][1]
    assert already[0].subcategory == "Rent"
    assert already[0].already_created is True
    assert len(result.pages) == 2

    finance.income_rows = (
        *finance.income_rows,
        Income("bonus", "Bonus", TARGET, Decimal("1")),
    )
    with pytest.raises(BudgetPlanningFreshnessError, match="evidence changed"):
        asyncio.run(service.apply_plan(draft))
