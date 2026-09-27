"""Notion persistence for normalized bank movements."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from notion_client import AsyncClient

from bank_account.models import BankTransaction

VisibleKey = tuple[str, ...]
_MONEY_PLACES = Decimal("0.01")


class BankTransactionSink(Protocol):
    """Persistence operations needed by the import workflow."""

    async def validate_schema(self) -> None: ...

    async def existing_transactions(
        self, transactions: Sequence[BankTransaction]
    ) -> "ExistingTransactions": ...

    async def create(self, transaction: BankTransaction) -> str: ...


@dataclass(frozen=True, slots=True)
class BankProperties:
    uid: str = "UID"
    title: str = "Title"
    date: str = "Date"
    direction: str = "Select"
    amount: str = "Amount"
    balance_after: str = "Balance"
    description: str = "Description"
    action: str = "Action"

    def expected_types(self) -> dict[str, str]:
        return {
            self.uid: "rich_text",
            self.title: "title",
            self.date: "date",
            self.direction: "select",
            self.amount: "number",
            self.balance_after: "number",
            self.description: "rich_text",
            self.action: "rich_text",
        }


@dataclass(frozen=True, slots=True)
class ExistingTransaction:
    page_id: str
    visible_key: VisibleKey


@dataclass(frozen=True, slots=True)
class ExistingTransactions:
    by_uid: Mapping[str, ExistingTransaction]
    by_visible_key: Mapping[VisibleKey, str]


class NotionBankRepository:
    """Write transactions and detect rows already present in Notion."""

    def __init__(
        self,
        token: str,
        *,
        database_id: str,
        data_source_id: str | None = None,
        api_version: str = "2026-03-11",
        properties: BankProperties | None = None,
        client: AsyncClient | None = None,
    ) -> None:
        self.database_id = database_id
        self._data_source_id = data_source_id
        self.props = properties or BankProperties()
        self._client = client or AsyncClient(auth=token, notion_version=api_version)
        self._schema_validated = False

    async def __aenter__(self) -> "NotionBankRepository":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def data_source_id(self) -> str:
        if self._data_source_id:
            return self._data_source_id
        database = await self._client.databases.retrieve(database_id=self.database_id)
        sources = database.get("data_sources", []) if isinstance(database, dict) else []
        ids: list[str] = []
        for item in sources:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            if isinstance(item_id, str):
                ids.append(item_id)
        if len(ids) != 1:
            raise RuntimeError(
                "The Notion database must expose exactly one data source, or "
                "BANK_ACCOUNT_NOTION_DATA_SOURCE_ID must be set"
            )
        data_source_id = ids[0]
        self._data_source_id = data_source_id
        return data_source_id

    async def validate_schema(self) -> None:
        if self._schema_validated:
            return
        source = await self._client.data_sources.retrieve(
            data_source_id=await self.data_source_id()
        )
        raw_properties = source.get("properties") if isinstance(source, dict) else None
        properties = raw_properties if isinstance(raw_properties, dict) else {}
        problems: list[str] = []
        for name, expected_type in self.props.expected_types().items():
            value = properties.get(name)
            actual_type = value.get("type") if isinstance(value, dict) else None
            if actual_type != expected_type:
                problems.append(
                    f"{name!r}: expected {expected_type}, found {actual_type or 'missing'}"
                )
        if problems:
            raise RuntimeError("Notion bank schema mismatch: " + "; ".join(problems))
        self._schema_validated = True

    async def existing_transactions(
        self, transactions: Sequence[BankTransaction]
    ) -> ExistingTransactions:
        if not transactions:
            return ExistingTransactions(by_uid={}, by_visible_key={})
        start = min(item.occurred_on for item in transactions).isoformat()
        end = max(item.occurred_on for item in transactions).isoformat()
        filter_ = {
            "and": [
                {"property": self.props.date, "date": {"on_or_after": start}},
                {"property": self.props.date, "date": {"on_or_before": end}},
            ]
        }
        pages: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"filter": filter_, "page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            response = await self._client.data_sources.query(
                data_source_id=await self.data_source_id(), **body
            )
            pages.extend(item for item in response.get("results", []) if isinstance(item, dict))
            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")
            if not isinstance(cursor, str):
                raise RuntimeError("Notion pagination returned no cursor")
        by_uid: dict[str, ExistingTransaction] = {}
        by_visible_key: dict[VisibleKey, str] = {}
        for page in pages:
            key = _page_visible_key(page, self.props)
            page_id = page.get("id")
            if key is not None and isinstance(page_id, str):
                by_visible_key[key] = page_id
                uid = _page_uid(page, self.props)
                if uid:
                    existing = by_uid.get(uid)
                    if existing is not None and existing.visible_key != key:
                        raise RuntimeError(
                            f"Notion contains conflicting bank movements with UID {uid!r}"
                        )
                    by_uid[uid] = ExistingTransaction(page_id, key)
        return ExistingTransactions(by_uid=by_uid, by_visible_key=by_visible_key)

    async def create(self, transaction: BankTransaction) -> str:
        properties = {
            self.props.uid: _rich_text(transaction.uid),
            self.props.title: _title(transaction.title),
            self.props.date: {"date": {"start": transaction.occurred_on.isoformat()}},
            self.props.direction: {"select": {"name": transaction.direction.value}},
            self.props.amount: {"number": float(transaction.amount)},
            self.props.balance_after: {"number": float(transaction.balance_after)},
            self.props.description: _rich_text(transaction.description),
            self.props.action: _rich_text(transaction.action),
        }
        page = await self._client.pages.create(
            parent={"type": "data_source_id", "data_source_id": await self.data_source_id()},
            properties=properties,
        )
        page_id = page.get("id") if isinstance(page, dict) else None
        if not isinstance(page_id, str) or not page_id:
            raise RuntimeError("Notion created a bank movement without returning a page ID")
        return page_id


def _title(value: str) -> dict[str, object]:
    return {"title": [_text_item(value)]}


def _rich_text(value: str) -> dict[str, object]:
    return {"rich_text": [_text_item(value)] if value else []}


def _text_item(value: str) -> dict[str, object]:
    return {"type": "text", "text": {"content": value}}


def _page_visible_key(page: Mapping[str, Any], props: BankProperties) -> VisibleKey | None:
    properties = page.get("properties")
    values = properties if isinstance(properties, dict) else {}
    occurred_on = _date(values.get(props.date))
    direction = _select(values.get(props.direction))
    amount = _number(values.get(props.amount))
    balance = _number(values.get(props.balance_after))
    if occurred_on is None or direction is None or amount is None or balance is None:
        return None
    return (
        _text(values.get(props.title)),
        occurred_on,
        direction,
        _money_text(amount),
        _money_text(balance),
        _text(values.get(props.description)),
        _text(values.get(props.action)),
    )


def _page_uid(page: Mapping[str, Any], props: BankProperties) -> str:
    properties = page.get("properties")
    values = properties if isinstance(properties, dict) else {}
    return _text(values.get(props.uid))


def _text(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    items = value.get("title") or value.get("rich_text") or []
    if not isinstance(items, list):
        return ""
    return "".join(
        item.get("plain_text", "")
        if isinstance(item, dict) and isinstance(item.get("plain_text"), str)
        else ""
        for item in items
    ).strip()


def _date(value: object) -> str | None:
    item = value.get("date") if isinstance(value, dict) else None
    start = item.get("start") if isinstance(item, dict) else None
    return start[:10] if isinstance(start, str) else None


def _select(value: object) -> str | None:
    item = value.get("select") if isinstance(value, dict) else None
    name = item.get("name") if isinstance(item, dict) else None
    return name if isinstance(name, str) else None


def _number(value: object) -> Decimal | None:
    raw = value.get("number") if isinstance(value, dict) else None
    if raw is None or isinstance(raw, bool):
        return None
    return Decimal(str(raw))


def _money_text(value: Decimal) -> str:
    return str(value.quantize(_MONEY_PLACES, rounding=ROUND_HALF_UP))
