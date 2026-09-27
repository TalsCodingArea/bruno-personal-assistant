"""Notion adapter for the structured, versioned financial profile."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from financial_agent.domain.operational_context import OPERATIONAL_CONTEXT_SCOPE
from financial_agent.domain.profile import (
    FinancialProfileEntry,
    FinancialProfileUpdateDraft,
    ProfileKind,
    ProfileStatus,
)
from financial_agent.integrations.notion import NotionGateway
from financial_agent.integrations.notion_schema import FinancialProfileProperties


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


def _option(page: Mapping[str, Any], name: str) -> str:
    value = _property(page, name)
    prop_type = value.get("type")
    option = value.get(prop_type) if prop_type in {"select", "status"} else None
    selected = option.get("name") if isinstance(option, dict) else None
    return selected if isinstance(selected, str) else ""


def _multi_select(page: Mapping[str, Any], name: str) -> tuple[str, ...]:
    items = _property(page, name).get("multi_select")
    if not isinstance(items, list):
        return ()
    return tuple(
        item["name"]
        for item in items
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    )


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
    if isinstance(prefix, str) and prefix:
        return f"{prefix}-{value['number']}"
    return str(value["number"])


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


class NotionFinancialProfileRepository:
    """Read and version structured profile entries in the Financial Rules source."""

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

    def _map_page(self, page: Mapping[str, Any]) -> FinancialProfileEntry:
        page_id = page.get("id")
        if not isinstance(page_id, str):
            raise ValueError("Financial profile page is missing an ID")
        try:
            kind = ProfileKind(_option(page, self.props.kind))
            status = ProfileStatus(_option(page, self.props.status))
        except ValueError as exc:
            raise ValueError(
                f"Financial profile page {page_id} has an invalid select value"
            ) from exc
        key = _text(page, self.props.key)
        statement = _text(page, self.props.statement)
        if not key or not statement:
            raise ValueError(f"Financial profile page {page_id} requires Key and Statement")
        return FinancialProfileEntry(
            page_id=page_id,
            name=_text(page, self.props.name),
            key=key,
            kind=kind,
            scopes=_multi_select(page, self.props.scope),
            statement=statement,
            status=status,
            supersedes=_relations(page, self.props.supersedes),
            operation_id=_unique_id(page, self.props.operation_id),
            created_at=_timestamp(page, "created_time"),
            last_edited_at=_timestamp(page, "last_edited_time"),
        )

    async def active_entries(
        self, *, key: str | None = None, limit: int = 100
    ) -> tuple[FinancialProfileEntry, ...]:
        filters: list[dict[str, Any]] = [
            {
                "property": self.props.status,
                "status": {"equals": ProfileStatus.ACTIVE.value},
            },
            {
                "property": self.props.scope,
                "multi_select": {"does_not_contain": OPERATIONAL_CONTEXT_SCOPE},
            },
        ]
        if key is not None:
            filters.append({"property": self.props.key, "rich_text": {"equals": key}})
        filter_: dict[str, Any] = filters[0] if len(filters) == 1 else {"and": filters}
        pages = await self.notion.query_all(
            self.data_source_id,
            filter_=filter_,
            sorts=[{"timestamp": "last_edited_time", "direction": "descending"}],
            filter_properties=[
                self.props.name,
                self.props.key,
                self.props.kind,
                self.props.scope,
                self.props.statement,
                self.props.status,
                self.props.supersedes,
                self.props.operation_id,
            ],
            max_results=limit,
        )
        return tuple(self._map_page(page) for page in pages)

    async def create_active_version(
        self, draft: FinancialProfileUpdateDraft
    ) -> FinancialProfileEntry:
        properties: dict[str, Any] = {
            self.props.name: _title(draft.name),
            self.props.key: _rich_text(draft.key),
            self.props.kind: {"select": {"name": draft.kind.value}},
            self.props.scope: {
                "multi_select": [{"name": scope} for scope in draft.scopes]
            },
            self.props.statement: _rich_text(draft.statement),
            self.props.status: {"status": {"name": ProfileStatus.ACTIVE.value}},
        }
        if draft.current_page_id is not None:
            properties[self.props.supersedes] = {
                "relation": [{"id": draft.current_page_id}]
            }
        children = [
            _paragraph(f"Statement: {draft.statement}"),
            _paragraph(f"Rationale: {draft.rationale}"),
        ]
        page = await self.notion.create_page(
            self.data_source_id, properties, children=children
        )
        return self._map_page(page)

    async def mark_superseded(self, page_id: str) -> None:
        await self.notion.update_page(
            page_id,
            {self.props.status: {"status": {"name": ProfileStatus.SUPERSEDED.value}}},
        )
