"""Notion persistence adapter for versioned operational budget context."""

import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from financial_agent.domain.operational_context import (
    BUDGET_MONITORING_SCOPE,
    OPERATIONAL_CONTEXT_KEY_PREFIX,
    OPERATIONAL_CONTEXT_SCOPE,
    OperationalContextEntry,
    OperationalContextKind,
    OperationalContextLifecycle,
    OperationalContextState,
    OperationalContextVersionConflict,
)
from financial_agent.domain.profile import ProfileKind, ProfileStatus
from financial_agent.integrations.notion import NotionGateway
from financial_agent.integrations.notion_schema import FinancialProfileProperties

SCHEMA_VERSION = 1


def _properties(page: Mapping[str, Any]) -> Mapping[str, Any]:
    value = page.get("properties")
    return value if isinstance(value, dict) else {}


def _property(page: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = _properties(page).get(name)
    return value if isinstance(value, dict) else {}


def _text(page: Mapping[str, Any], name: str) -> str:
    value = _property(page, name)
    prop_type = value.get("type")
    items = value.get(prop_type) if prop_type in {"title", "rich_text"} else []
    if not isinstance(items, list):
        return ""
    return "".join(
        item.get("plain_text", "") for item in items if isinstance(item, dict)
    ).strip()


def _relations(page: Mapping[str, Any], name: str) -> tuple[str, ...]:
    items = _property(page, name).get("relation")
    if not isinstance(items, list):
        return ()
    return tuple(
        item["id"]
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    )


def _unique_id(page: Mapping[str, Any], name: str) -> str | None:
    value = _property(page, name).get("unique_id")
    if not isinstance(value, dict) or not isinstance(value.get("number"), int):
        return None
    prefix = value.get("prefix")
    return (
        f"{prefix}-{value['number']}"
        if isinstance(prefix, str) and prefix
        else str(value["number"])
    )


def _timestamp(page: Mapping[str, Any], name: str) -> datetime | None:
    value = page.get(name)
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _rich_text(value: str) -> dict[str, list[dict[str, object]]]:
    return {"rich_text": [{"type": "text", "text": {"content": value}}]}


def _title(value: str) -> dict[str, list[dict[str, object]]]:
    return {"title": [{"type": "text", "text": {"content": value}}]}


def _paragraph(value: str) -> dict[str, object]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": value}}]
        },
    }


def _state_payload(state: OperationalContextState) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "key": state.key,
        "kind": state.kind.value,
        "month": state.month.isoformat(),
        "subcategory": state.subcategory,
        "lifecycle": state.lifecycle.value,
        "first_observed_on": state.first_observed_on.isoformat(),
        "last_observed_on": state.last_observed_on.isoformat(),
        "resolved_on": state.resolved_on.isoformat() if state.resolved_on else None,
        "occurrence_count": state.occurrence_count,
        "current_signal": state.current_signal,
        "acknowledged_signal": state.acknowledged_signal,
        "amount": _decimal_text(state.amount),
        "percent": _decimal_text(state.percent),
        "band": _decimal_text(state.band),
        "proposed_adjustment_amount": _decimal_text(
            state.proposed_adjustment_amount
        ),
        "unresolved_amount": _decimal_text(state.unresolved_amount),
    }


def _state_json(state: OperationalContextState) -> str:
    return json.dumps(_state_payload(state), ensure_ascii=False, separators=(",", ":"))


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _page_children(state: OperationalContextState) -> list[dict[str, object]]:
    subject = state.subcategory or "Overall budget"
    evidence = [f"subject={subject}", f"observed_through={state.last_observed_on}"]
    if state.amount is not None:
        evidence.append(f"amount_ils={state.amount}")
    if state.percent is not None:
        evidence.append(f"deviation_percent={state.percent}")
    if state.band is not None:
        evidence.append(f"projection_band={state.band}")
    if state.proposed_adjustment_amount is not None:
        evidence.append(
            f"proposed_adjustment_ils={state.proposed_adjustment_amount}"
        )
    if state.unresolved_amount is not None:
        evidence.append(f"unresolved_ils={state.unresolved_amount}")
    return [
        _paragraph("System-maintained operational context. Do not edit manually."),
        _paragraph(
            f"Lifecycle: {state.lifecycle.value}; occurrences: {state.occurrence_count}"
        ),
        _paragraph("Evidence: " + "; ".join(evidence)),
        _paragraph(
            "Presentation: "
            f"current={state.current_signal}; "
            f"acknowledged={state.acknowledged_signal or 'not yet'}"
        ),
    ]


def _required_string(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Operational context payload requires {name}")
    return value


def _optional_string(payload: Mapping[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Operational context payload {name} must be text or null")
    return value


def _optional_decimal(payload: Mapping[str, Any], name: str) -> Decimal | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Operational context payload {name} must be decimal text")
    return Decimal(value)


def _parse_state(
    page: Mapping[str, Any], props: FinancialProfileProperties
) -> OperationalContextState:
    statement = _text(page, props.statement)
    try:
        payload = json.loads(statement)
    except json.JSONDecodeError as exc:
        raise ValueError("Operational context Statement contains invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported operational context schema version")
    key = _required_string(payload, "key")
    if key != _text(page, props.key):
        raise ValueError("Operational context Key differs from its Statement payload")
    occurrence_count = payload.get("occurrence_count")
    if not isinstance(occurrence_count, int) or isinstance(occurrence_count, bool):
        raise ValueError("Operational context occurrence_count must be an integer")
    resolved = _optional_string(payload, "resolved_on")
    return OperationalContextState(
        key=key,
        name=_text(page, props.name),
        kind=OperationalContextKind(_required_string(payload, "kind")),
        month=date.fromisoformat(_required_string(payload, "month")),
        subcategory=_optional_string(payload, "subcategory"),
        lifecycle=OperationalContextLifecycle(_required_string(payload, "lifecycle")),
        first_observed_on=date.fromisoformat(
            _required_string(payload, "first_observed_on")
        ),
        last_observed_on=date.fromisoformat(
            _required_string(payload, "last_observed_on")
        ),
        resolved_on=date.fromisoformat(resolved) if resolved else None,
        occurrence_count=occurrence_count,
        current_signal=_required_string(payload, "current_signal"),
        acknowledged_signal=_optional_string(payload, "acknowledged_signal"),
        amount=_optional_decimal(payload, "amount"),
        percent=_optional_decimal(payload, "percent"),
        band=_optional_decimal(payload, "band"),
        proposed_adjustment_amount=_optional_decimal(
            payload, "proposed_adjustment_amount"
        ),
        unresolved_amount=_optional_decimal(payload, "unresolved_amount"),
    )


class NotionOperationalContextRepository:
    """Store one Active version per operational identity in Financial Rules."""

    def __init__(
        self,
        notion: NotionGateway,
        data_source_id: str,
        *,
        properties: FinancialProfileProperties | None = None,
    ) -> None:
        self.notion = notion
        self.data_source_id = data_source_id
        self.props = properties or FinancialProfileProperties()

    def _map_page(self, page: Mapping[str, Any]) -> OperationalContextEntry:
        page_id = page.get("id")
        if not isinstance(page_id, str):
            raise ValueError("Operational context page is missing an ID")
        return OperationalContextEntry(
            page_id=page_id,
            state=_parse_state(page, self.props),
            supersedes=_relations(page, self.props.supersedes),
            operation_id=_unique_id(page, self.props.operation_id),
            created_at=_timestamp(page, "created_time"),
            last_edited_at=_timestamp(page, "last_edited_time"),
        )

    async def current_for_month(
        self, month: date
    ) -> tuple[OperationalContextEntry, ...]:
        prefix = f"{OPERATIONAL_CONTEXT_KEY_PREFIX}{month.replace(day=1):%Y-%m}."
        return await self._query_current(
            {"property": self.props.key, "rich_text": {"starts_with": prefix}}
        )

    async def current_for_key(
        self, key: str
    ) -> tuple[OperationalContextEntry, ...]:
        return await self._query_current(
            {"property": self.props.key, "rich_text": {"equals": key}}
        )

    async def _query_current(
        self, identity_filter: dict[str, Any]
    ) -> tuple[OperationalContextEntry, ...]:
        pages = await self.notion.query_all(
            self.data_source_id,
            filter_={
                "and": [
                    {
                        "property": self.props.status,
                        "status": {"equals": ProfileStatus.ACTIVE.value},
                    },
                    {
                        "property": self.props.scope,
                        "multi_select": {"contains": OPERATIONAL_CONTEXT_SCOPE},
                    },
                    identity_filter,
                ]
            },
            sorts=[{"timestamp": "last_edited_time", "direction": "descending"}],
            filter_properties=[
                self.props.name,
                self.props.key,
                self.props.scope,
                self.props.statement,
                self.props.status,
                self.props.supersedes,
                self.props.operation_id,
            ],
        )
        return tuple(self._map_page(page) for page in pages)

    async def save_version(
        self,
        state: OperationalContextState,
        expected: OperationalContextEntry | None,
    ) -> OperationalContextEntry:
        current = await self.current_for_key(state.key)
        exact = tuple(entry for entry in current if entry.state == state)
        if len(exact) == 1 and all(
            entry.page_id in exact[0].supersedes for entry in current if entry != exact[0]
        ):
            for predecessor in current:
                if predecessor != exact[0]:
                    await self._mark_superseded(predecessor.page_id)
            return exact[0]
        if len(current) > 1:
            raise OperationalContextVersionConflict(
                f"Multiple current operational versions found for {state.key!r}"
            )
        actual = current[0] if current else None
        self._assert_expected(expected, actual)
        statement = _state_json(state)
        if len(statement) > 1800:
            raise ValueError("Operational context payload exceeds compact Statement limit")
        properties: dict[str, Any] = {
            self.props.name: _title(state.name),
            self.props.key: _rich_text(state.key),
            self.props.kind: {"select": {"name": ProfileKind.DECISION.value}},
            self.props.scope: {
                "multi_select": [
                    {"name": OPERATIONAL_CONTEXT_SCOPE},
                    {"name": BUDGET_MONITORING_SCOPE},
                ]
            },
            self.props.statement: _rich_text(statement),
            self.props.status: {"status": {"name": ProfileStatus.ACTIVE.value}},
        }
        if actual is not None:
            properties[self.props.supersedes] = {
                "relation": [{"id": actual.page_id}]
            }
        page = await self.notion.create_page(
            self.data_source_id,
            properties,
            children=_page_children(state),
        )
        created = self._map_page(page)
        if actual is not None:
            await self._mark_superseded(actual.page_id)
        return created

    async def _mark_superseded(self, page_id: str) -> None:
        await self.notion.update_page(
            page_id,
            {self.props.status: {"status": {"name": ProfileStatus.SUPERSEDED.value}}},
        )

    @staticmethod
    def _assert_expected(
        expected: OperationalContextEntry | None,
        actual: OperationalContextEntry | None,
    ) -> None:
        if (expected is None) != (actual is None):
            raise OperationalContextVersionConflict(
                "Operational context changed after reconciliation"
            )
        if expected is None or actual is None:
            return
        if (
            expected.page_id != actual.page_id
            or expected.last_edited_at != actual.last_edited_at
        ):
            raise OperationalContextVersionConflict(
                "Operational context was edited after reconciliation"
            )
