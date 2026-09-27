"""Notion boundary tests for bank movement persistence."""

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any, cast

from notion_client import AsyncClient

from bank_account.models import BankTransaction, TransactionDirection
from bank_account.notion import BankProperties, ExistingTransaction, NotionBankRepository


class FakeDatabases:
    async def retrieve(self, *, database_id: str) -> dict[str, object]:
        assert database_id == "database-id"
        return {"data_sources": [{"id": "source-id"}]}


class FakeDataSources:
    def __init__(self, properties: dict[str, object], pages: list[dict[str, object]]) -> None:
        self.properties = properties
        self.pages = pages

    async def retrieve(self, *, data_source_id: str) -> dict[str, object]:
        assert data_source_id == "source-id"
        return {"properties": self.properties}

    async def query(self, *, data_source_id: str, **kwargs: object) -> dict[str, object]:
        assert data_source_id == "source-id"
        assert "filter" in kwargs
        return {"results": self.pages, "has_more": False}


class FakePages:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> dict[str, object]:
        self.created.append(kwargs)
        return {"id": "created-page"}


class FakeClient:
    def __init__(self, properties: dict[str, object], pages: list[dict[str, object]]) -> None:
        self.databases = FakeDatabases()
        self.data_sources = FakeDataSources(properties, pages)
        self.pages = FakePages()

    async def aclose(self) -> None:
        return None


def _schema() -> dict[str, object]:
    return {name: {"type": type_} for name, type_ in BankProperties().expected_types().items()}


def _transaction() -> BankTransaction:
    return BankTransaction(
        fingerprint="fingerprint",
        uid="123",
        title="Salary",
        occurred_on=date(2026, 9, 10),
        direction=TransactionDirection.POSITIVE,
        amount=Decimal("10000.50"),
        balance_after=Decimal("15000.50"),
        description="אסמכתא: 123",
        action="משכורת",
    )


def test_validates_schema_detects_existing_row_and_creates_page() -> None:
    transaction = _transaction()
    props = BankProperties()
    page = {
        "id": "existing-page",
        "properties": {
            props.uid: {"rich_text": [{"plain_text": transaction.uid}]},
            props.title: {"title": [{"plain_text": transaction.title}]},
            props.date: {"date": {"start": transaction.occurred_on.isoformat()}},
            props.direction: {"select": {"name": transaction.direction.value}},
            props.amount: {"number": 10000.5},
            props.balance_after: {"number": 15000.5},
            props.description: {"rich_text": [{"plain_text": transaction.description}]},
            props.action: {"rich_text": [{"plain_text": transaction.action}]},
        },
    }
    fake = FakeClient(_schema(), [page])
    repository = NotionBankRepository(
        "token",
        database_id="database-id",
        client=cast(AsyncClient, cast(Any, fake)),
    )

    async def run() -> tuple[dict[str, ExistingTransaction], str]:
        await repository.validate_schema()
        found = await repository.existing_transactions([transaction])
        page_id = await repository.create(transaction)
        return dict(found.by_uid), page_id

    existing, page_id = asyncio.run(run())

    assert existing[transaction.uid].page_id == "existing-page"
    assert existing[transaction.uid].visible_key == transaction.visible_key()
    assert page_id == "created-page"
    created = fake.pages.created[0]
    assert created["parent"] == {
        "type": "data_source_id",
        "data_source_id": "source-id",
    }
    properties = created["properties"]
    assert isinstance(properties, dict)
    assert properties[props.uid] == {
        "rich_text": [{"type": "text", "text": {"content": transaction.uid}}]
    }
