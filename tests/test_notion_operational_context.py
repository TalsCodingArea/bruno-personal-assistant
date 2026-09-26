"""Tests for operational-context storage in the Financial Rules data source."""

import asyncio
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal

from financial_agent.domain.operational_context import (
    OperationalContextKind,
    OperationalContextLifecycle,
    OperationalContextState,
)
from financial_agent.integrations.notion_operational_context import (
    NotionOperationalContextRepository,
)

from tests.fakes import FakeNotion


def state() -> OperationalContextState:
    return OperationalContextState(
        key="monitoring.runtime.2026-08.projection_deviation.abc123",
        name="2026-08 · Groceries · Projection Deviation",
        kind=OperationalContextKind.PROJECTION_DEVIATION,
        month=date(2026, 8, 1),
        subcategory="Groceries",
        lifecycle=OperationalContextLifecycle.ACTIVE,
        first_observed_on=date(2026, 8, 20),
        last_observed_on=date(2026, 8, 20),
        resolved_on=None,
        occurrence_count=1,
        current_signal="projection_deviation:25",
        amount=Decimal("93"),
        percent=Decimal("30"),
        band=Decimal("25"),
    )


def page(
    page_id: str,
    value: OperationalContextState,
    *,
    supersedes: tuple[str, ...] = (),
) -> dict[str, object]:
    payload = {
        "schema_version": 1,
        "key": value.key,
        "kind": value.kind.value,
        "month": value.month.isoformat(),
        "subcategory": value.subcategory,
        "lifecycle": value.lifecycle.value,
        "first_observed_on": value.first_observed_on.isoformat(),
        "last_observed_on": value.last_observed_on.isoformat(),
        "resolved_on": value.resolved_on.isoformat() if value.resolved_on else None,
        "occurrence_count": value.occurrence_count,
        "current_signal": value.current_signal,
        "acknowledged_signal": value.acknowledged_signal,
        "amount": format(value.amount, "f") if value.amount is not None else None,
        "percent": format(value.percent, "f") if value.percent is not None else None,
        "band": format(value.band, "f") if value.band is not None else None,
        "proposed_adjustment_amount": None,
        "unresolved_amount": None,
    }
    return {
        "id": page_id,
        "created_time": "2026-08-20T04:00:00.000Z",
        "last_edited_time": "2026-08-20T04:00:00.000Z",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": value.name}]},
            "Key": {"type": "rich_text", "rich_text": [{"plain_text": value.key}]},
            "Scope": {
                "type": "multi_select",
                "multi_select": [
                    {"name": "Operational Context"},
                    {"name": "Budget Monitoring"},
                ],
            },
            "Statement": {
                "type": "rich_text",
                "rich_text": [{"plain_text": json.dumps(payload)}],
            },
            "Status": {"type": "status", "status": {"name": "Active"}},
            "Supersedes": {
                "type": "relation",
                "relation": [{"id": item} for item in supersedes],
            },
            "Operation ID": {
                "type": "unique_id",
                "unique_id": {"prefix": "CTX", "number": 9},
            },
        },
    }


def test_repository_creates_compact_versioned_operational_page() -> None:
    notion = FakeNotion({"profile": []})
    repository = NotionOperationalContextRepository(notion, "profile")

    created = asyncio.run(repository.save_version(state(), None))

    assert created.state == state()
    properties = notion.created[0]["properties"]
    assert properties["Scope"] == {
        "multi_select": [
            {"name": "Operational Context"},
            {"name": "Budget Monitoring"},
        ]
    }
    payload = json.loads(properties["Statement"]["rich_text"][0]["text"]["content"])
    assert payload["schema_version"] == 1
    assert payload["amount"] == "93.00"
    assert payload["current_signal"] == "projection_deviation:25"
    children = notion.created[0]["children"]
    assert "Do not edit manually" in children[0]["paragraph"]["rich_text"][0]["text"][
        "content"
    ]
    assert "amount_ils=93.00" in children[2]["paragraph"]["rich_text"][0]["text"][
        "content"
    ]
    assert notion.updated == []
    query_filter = notion.queries[0]["filter"]["and"]
    assert {
        "property": "Scope",
        "multi_select": {"contains": "Operational Context"},
    } in query_filter


def test_repository_versions_current_page_with_optimistic_predecessor() -> None:
    initial = state()
    notion = FakeNotion({"profile": [page("old-page", initial)]})
    repository = NotionOperationalContextRepository(notion, "profile")
    current = asyncio.run(repository.current_for_month(date(2026, 8, 1)))[0]
    updated = replace(
        initial,
        last_observed_on=date(2026, 8, 21),
        occurrence_count=2,
        current_signal="projection_deviation:50",
        band=Decimal("50"),
    )

    asyncio.run(repository.save_version(updated, current))

    assert notion.created[0]["properties"]["Supersedes"] == {
        "relation": [{"id": "old-page"}]
    }
    assert notion.updated == [
        {
            "page_id": "old-page",
            "properties": {"Status": {"status": {"name": "Superseded"}}},
        }
    ]


def test_repository_retry_finishes_supersession_without_duplicate_create() -> None:
    initial = state()
    updated = replace(
        initial,
        last_observed_on=date(2026, 8, 21),
        occurrence_count=2,
    )
    notion = FakeNotion(
        {
            "profile": [
                page("new-page", updated, supersedes=("old-page",)),
                page("old-page", initial),
            ]
        }
    )
    repository = NotionOperationalContextRepository(notion, "profile")

    result = asyncio.run(repository.save_version(updated, None))

    assert result.page_id == "new-page"
    assert notion.created == []
    assert notion.updated == [
        {
            "page_id": "old-page",
            "properties": {"Status": {"status": {"name": "Superseded"}}},
        }
    ]
