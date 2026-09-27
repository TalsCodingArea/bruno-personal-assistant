"""Read-only mapping for the imported Bank Movement Notion database."""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from financial_agent.domain.models import BankMovement
from financial_agent.domain.money import money_from_api
from financial_agent.integrations.notion import NotionGateway
from financial_agent.integrations.notion_schema import BankMovementProperties


def _property(page: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    properties = page.get("properties")
    value = properties.get(name) if isinstance(properties, dict) else None
    return value if isinstance(value, dict) else {}


def _text(page: Mapping[str, Any], name: str) -> str:
    value = _property(page, name)
    items = value.get("title") or value.get("rich_text") or []
    return "".join(
        item.get("plain_text", "")
        for item in items
        if isinstance(item, dict)
    ).strip()


def _date(page: Mapping[str, Any], name: str) -> date | None:
    value = _property(page, name).get("date")
    start = value.get("start") if isinstance(value, dict) else None
    return date.fromisoformat(start[:10]) if isinstance(start, str) else None


def _number(page: Mapping[str, Any], name: str) -> Decimal | None:
    raw = _property(page, name).get("number")
    return None if raw is None else money_from_api(raw)


def _select(page: Mapping[str, Any], name: str) -> str:
    value = _property(page, name).get("select")
    selected = value.get("name") if isinstance(value, dict) else None
    return selected if isinstance(selected, str) else ""


class NotionBankMovementReader:
    """Query Bank Movement rows without exposing any mutation operation."""

    def __init__(
        self,
        notion: NotionGateway,
        *,
        data_source_id: str | None,
        database_id: str | None,
        properties: BankMovementProperties | None = None,
    ) -> None:
        if not data_source_id and not database_id:
            raise ValueError("A bank data-source ID or database ID is required")
        self.notion = notion
        self._data_source_id = data_source_id
        self._database_id = database_id
        self.props = properties or BankMovementProperties()

    async def _source_id(self) -> str:
        if self._data_source_id:
            return self._data_source_id
        assert self._database_id is not None
        self._data_source_id = await self.notion.resolve_database_data_source(
            self._database_id
        )
        return self._data_source_id

    async def latest(self, as_of: date) -> BankMovement | None:
        rows = await self.notion.query_all(
            await self._source_id(),
            filter_={"property": self.props.date, "date": {"on_or_before": as_of.isoformat()}},
            sorts=[{"property": self.props.date, "direction": "descending"}],
            max_results=1,
        )
        return self._movement(rows[0]) if rows else None

    async def movements(
        self, start_date: date, end_date: date, *, limit: int = 50
    ) -> tuple[BankMovement, ...]:
        rows = await self.notion.query_all(
            await self._source_id(),
            filter_={
                "and": [
                    {"property": self.props.date, "date": {"on_or_after": start_date.isoformat()}},
                    {"property": self.props.date, "date": {"on_or_before": end_date.isoformat()}},
                ]
            },
            sorts=[{"property": self.props.date, "direction": "descending"}],
            max_results=limit,
        )
        return tuple(item for row in rows if (item := self._movement(row)) is not None)

    def _movement(self, page: Mapping[str, Any]) -> BankMovement | None:
        occurred_on = _date(page, self.props.date)
        amount = _number(page, self.props.amount)
        balance = _number(page, self.props.balance_after)
        page_id = page.get("id")
        if (
            occurred_on is None
            or amount is None
            or balance is None
            or not isinstance(page_id, str)
        ):
            return None
        return BankMovement(
            id=page_id,
            title=_text(page, self.props.title),
            occurred_on=occurred_on,
            direction=_select(page, self.props.direction),
            amount=amount,
            balance_after=balance,
            description=_text(page, self.props.description),
            action=_text(page, self.props.action),
        )
