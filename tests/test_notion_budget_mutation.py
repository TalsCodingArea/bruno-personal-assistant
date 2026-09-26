"""Tests for idempotent Notion budget writes and rollback."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from financial_agent.domain.budget_mutation import (
    BudgetMutationApplyError,
    BudgetMutationFreshnessError,
    BudgetMutationItem,
    BudgetMutationProposal,
    BudgetMutationStatus,
    BudgetPreferenceReview,
    VerifiedBudgetMutation,
)
from financial_agent.domain.models import ProgressiveMode
from financial_agent.integrations.notion_budget_mutation import NotionBudgetMutationRepository

EDITED = datetime(2026, 8, 20, 3, 55, tzinfo=UTC)
ADJUSTED = datetime(2026, 8, 20, 4, 0, tzinfo=UTC)


def page(page_id: str, name: str, amount: int) -> dict[str, Any]:
    return {
        "id": page_id,
        "last_edited_time": EDITED.isoformat(),
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": name}]},
            "Date": {"type": "date", "date": {"start": "2026-08-01"}},
            "Budget": {"type": "number", "number": amount},
            "Progressive": {
                "type": "select",
                "select": {"name": "Accumulated"},
            },
            "Volatility": {"type": "number", "number": 0.5},
            "Baseline Budget": {"type": "number", "number": None},
            "Last Adjustment ID": {"type": "rich_text", "rich_text": []},
            "Last Adjustment Reason": {"type": "rich_text", "rich_text": []},
            "Last Adjustment At": {"type": "date", "date": None},
        },
    }


class StatefulBudgetNotion:
    def __init__(
        self,
        pages: tuple[dict[str, Any], ...],
        *,
        fail_once: str | None = None,
        missing_schema_property: str | None = None,
    ):
        self.pages = {item["id"]: item for item in pages}
        self.fail_once = fail_once
        self.missing_schema_property = missing_schema_property
        self.updated: list[str] = []

    async def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        property_types = {
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
        if self.missing_schema_property is not None:
            property_types.pop(self.missing_schema_property)
        return {
            "id": data_source_id,
            "properties": {
                name: {"type": prop_type}
                for name, prop_type in property_types.items()
            },
        }

    async def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return self.pages[page_id]

    async def update_page(
        self, page_id: str, properties: Mapping[str, Any]
    ) -> dict[str, Any]:
        if self.fail_once == page_id:
            self.fail_once = None
            raise RuntimeError("simulated Notion failure")
        page_ = self.pages[page_id]
        for name, raw in properties.items():
            value = dict(raw)
            prop_type = next(iter(value))
            if prop_type in {"title", "rich_text"}:
                value[prop_type] = [
                    {
                        **item,
                        "plain_text": item.get("text", {}).get("content", ""),
                    }
                    for item in value[prop_type]
                ]
            page_["properties"][name] = {"type": prop_type, **value}
        page_["last_edited_time"] = ADJUSTED.isoformat()
        self.updated.append(page_id)
        return page_


def item(page_id: str, name: str, before: str, after: str) -> BudgetMutationItem:
    return BudgetMutationItem(
        page_id=page_id,
        subcategory=name,
        month=date(2026, 8, 1),
        amount_before=Decimal(before),
        amount_after=Decimal(after),
        progressive=ProgressiveMode.ACCUMULATED,
        volatility_percent=Decimal("50"),
        baseline_before=None,
        last_adjustment_id_before=None,
        last_adjustment_reason_before=None,
        last_adjustment_at_before=None,
        last_edited_at_before=EDITED,
    )


def verified(*items: BudgetMutationItem) -> VerifiedBudgetMutation:
    proposal = BudgetMutationProposal(
        operation_id="BADJ-TEST",
        report_fingerprint="fingerprint",
        month=date(2026, 8, 1),
        as_of=date(2026, 8, 20),
        monthly_income=Decimal("1000"),
        total_budget_before=sum((value.amount_before for value in items), Decimal()),
        total_budget_after=sum((value.amount_after for value in items), Decimal()),
        reason="Verified automatic rebalance",
        items=items,
        guidelines=(),
    )
    return VerifiedBudgetMutation(
        proposal=proposal,
        preference_review=BudgetPreferenceReview(True, "Approved", (), ()),
    )


def test_writer_applies_metadata_and_retry_is_idempotent() -> None:
    notion = StatefulBudgetNotion((page("groceries", "Groceries", 100),))
    repository = NotionBudgetMutationRepository(
        notion, "budgets", clock=lambda: ADJUSTED  # type: ignore[arg-type]
    )
    mutation = verified(item("groceries", "Groceries", "100", "130"))

    first = asyncio.run(repository.apply(mutation))
    second = asyncio.run(repository.apply(mutation))

    assert first.status is BudgetMutationStatus.APPLIED
    assert second.status is BudgetMutationStatus.ALREADY_APPLIED
    properties = notion.pages["groceries"]["properties"]
    assert properties["Budget"]["number"] == 130
    assert properties["Baseline Budget"]["number"] == 100
    assert properties["Last Adjustment ID"]["rich_text"][0]["plain_text"] == "BADJ-TEST"
    assert notion.updated == ["groceries"]


def test_writer_rejects_page_edited_after_analysis() -> None:
    stale = page("groceries", "Groceries", 100)
    stale["last_edited_time"] = datetime(2026, 8, 20, 3, 59, tzinfo=UTC).isoformat()
    repository = NotionBudgetMutationRepository(
        StatefulBudgetNotion((stale,)),
        "budgets",
        clock=lambda: ADJUSTED,  # type: ignore[arg-type]
    )

    with pytest.raises(BudgetMutationFreshnessError, match="edited after"):
        asyncio.run(
            repository.apply(verified(item("groceries", "Groceries", "100", "130")))
        )


def test_writer_validates_required_budget_schema_before_page_updates() -> None:
    notion = StatefulBudgetNotion(
        (page("groceries", "Groceries", 100),),
        missing_schema_property="Baseline Budget",
    )
    repository = NotionBudgetMutationRepository(
        notion, "budgets", clock=lambda: ADJUSTED  # type: ignore[arg-type]
    )

    with pytest.raises(BudgetMutationFreshnessError, match="Baseline Budget"):
        asyncio.run(
            repository.apply(verified(item("groceries", "Groceries", "100", "130")))
        )

    assert notion.updated == []


def test_second_page_failure_rolls_back_first_page() -> None:
    notion = StatefulBudgetNotion(
        (
            page("entertainment", "Entertainment", 400),
            page("groceries", "Groceries", 100),
        ),
        fail_once="groceries",
    )
    repository = NotionBudgetMutationRepository(
        notion, "budgets", clock=lambda: ADJUSTED  # type: ignore[arg-type]
    )
    mutation = verified(
        item("entertainment", "Entertainment", "400", "370"),
        item("groceries", "Groceries", "100", "130"),
    )

    with pytest.raises(BudgetMutationApplyError, match="rolled back"):
        asyncio.run(repository.apply(mutation))

    assert notion.pages["entertainment"]["properties"]["Budget"]["number"] == 400
    assert notion.pages["entertainment"]["properties"]["Baseline Budget"]["number"] is None
