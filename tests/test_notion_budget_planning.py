"""Notion payload, schema, and partial-failure tests for Budget page creation."""

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.domain.budget_planning import (
    BudgetCapBasis,
    BudgetPageDraft,
    BudgetPlanPartialFailure,
    BudgetPlanPurpose,
)
from app.domain.models import ProgressiveMode
from app.integrations.notion_budget_planning import NotionBudgetPlanCreationRepository
from app.services.budget_planning import BudgetPlanningService
from tests.fakes import FakeFinanceReader, FakeNotion
from tests.test_budget_planning import Rules

TARGET = date(2026, 9, 1)
NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)


class BudgetNotion(FakeNotion):
    def __init__(
        self,
        *,
        missing_property: str | None = None,
        fail_on_create: int | None = None,
    ) -> None:
        super().__init__()
        self.missing_property = missing_property
        self.fail_on_create = fail_on_create

    async def retrieve_data_source(self, data_source_id: str):  # type: ignore[no-untyped-def]
        types = {
            "Name": "title",
            "Date": "date",
            "Budget": "number",
            "Progressive": "select",
            "Volatility": "number",
            "Baseline Budget": "number",
            "Last Adjustment ID": "rich_text",
            "Last Adjustment Reason": "rich_text",
            "Last Adjustment At": "date",
        }
        if self.missing_property is not None:
            types.pop(self.missing_property)
        return {
            "id": data_source_id,
            "properties": {
                name: {"type": property_type}
                for name, property_type in types.items()
            },
        }

    async def create_page(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.fail_on_create == len(self.created) + 1:
            raise RuntimeError("simulated create failure")
        return await super().create_page(*args, **kwargs)


def draft_for(repository: NotionBudgetPlanCreationRepository):  # type: ignore[no-untyped-def]
    service = BudgetPlanningService(FakeFinanceReader(), Rules(), repository)
    context = asyncio.run(service.planning_context(TARGET))
    return service.draft_plan(
        context,
        (
            BudgetPageDraft(
                "Rent",
                Decimal("1000"),
                ProgressiveMode.DISCRETE,
                Decimal("0"),
                BudgetPlanPurpose.REGULAR,
                "Protected monthly rent payment.",
            ),
            BudgetPageDraft(
                "Insurance",
                Decimal("300"),
                ProgressiveMode.DISCRETE,
                Decimal("0"),
                BudgetPlanPurpose.REGULAR,
                "Reserved annual payment.",
            ),
        ),
        financial_cap=Decimal("1500"),
        cap_basis=BudgetCapBasis.USER_PROVIDED,
        cap_rationale="Tal provided the cap.",
    )


def test_writer_creates_complete_budget_pages_with_idempotency_metadata() -> None:
    notion = BudgetNotion()
    repository = NotionBudgetPlanCreationRepository(
        notion,
        "budgets",
        clock=lambda: NOW,
    )
    draft = draft_for(repository)

    result = asyncio.run(repository.create(draft, ()))

    assert len(result.pages) == 2
    rent = notion.created[0]["properties"]
    assert rent["Name"]["title"][0]["text"]["content"] == "Rent"
    assert rent["Date"] == {"date": {"start": "2026-09-01"}}
    assert rent["Budget"] == {"number": 1000}
    assert rent["Progressive"] == {"select": {"name": "Discrete"}}
    assert rent["Volatility"] == {"number": 0}
    assert rent["Baseline Budget"] == {"number": 1000}
    assert (
        rent["Last Adjustment ID"]["rich_text"][0]["text"]["content"]
        == draft.operation_id
    )


def test_writer_fails_before_creation_when_budget_schema_is_incomplete() -> None:
    notion = BudgetNotion(missing_property="Volatility")
    repository = NotionBudgetPlanCreationRepository(notion, "budgets")

    with pytest.raises(ValueError, match="Volatility"):
        asyncio.run(repository.create(draft_for(repository), ()))
    assert notion.created == []


def test_writer_reports_confirmed_pages_when_later_creation_fails() -> None:
    notion = BudgetNotion(fail_on_create=2)
    repository = NotionBudgetPlanCreationRepository(notion, "budgets")

    with pytest.raises(BudgetPlanPartialFailure, match="Rent"):
        asyncio.run(repository.create(draft_for(repository), ()))
    assert len(notion.created) == 1
