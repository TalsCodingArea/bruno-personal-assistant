"""Test doubles for external integrations."""

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from financial_agent.domain.models import Budget, Income, PlannedExpense, Transaction
from financial_agent.integrations.notion import NotionObject


class FakeNotion:
    """In-memory Notion gateway that also records requested filters."""

    def __init__(self, rows: Mapping[str, list[NotionObject]] | None = None) -> None:
        self.rows = {key: list(value) for key, value in (rows or {}).items()}
        self.queries: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.uploaded: list[dict[str, Any]] = []

    async def query_all(
        self,
        data_source_id: str,
        *,
        filter_: Mapping[str, Any] | None = None,
        sorts: Sequence[Mapping[str, Any]] = (),
        filter_properties: Sequence[str] = (),
        max_results: int | None = None,
    ) -> list[NotionObject]:
        self.queries.append(
            {
                "data_source_id": data_source_id,
                "filter": filter_,
                "sorts": sorts,
                "filter_properties": filter_properties,
                "max_results": max_results,
            }
        )
        result = list(self.rows.get(data_source_id, []))
        return result[:max_results] if max_results is not None else result

    async def retrieve_page(self, page_id: str) -> NotionObject:
        for rows in self.rows.values():
            for row in rows:
                if row.get("id") == page_id:
                    return row
        return {"id": page_id, "properties": {}}

    async def retrieve_data_source(self, data_source_id: str) -> NotionObject:
        return {"id": data_source_id, "properties": {"Name": {"type": "title"}}}

    async def retrieve_page_text(self, page_id: str, *, max_chars: int) -> str:
        return f"Content for {page_id}"[:max_chars]

    async def create_page(
        self,
        data_source_id: str,
        properties: Mapping[str, Any],
        *,
        children: Sequence[Mapping[str, Any]] = (),
    ) -> NotionObject:
        call = {
            "data_source_id": data_source_id,
            "properties": dict(properties),
            "children": list(children),
        }
        self.created.append(call)
        response_properties: dict[str, Any] = {}
        for name, value in properties.items():
            property_value = dict(value)
            property_type = next(iter(property_value))
            if property_type in {"title", "rich_text"}:
                items = property_value[property_type]
                property_value[property_type] = [
                    {
                        **item,
                        "plain_text": item.get("text", {}).get("content", ""),
                    }
                    for item in items
                ]
            response_properties[name] = {
                "id": name,
                "type": property_type,
                **property_value,
            }
        return {
            "id": f"created-{len(self.created)}",
            "created_time": "2026-08-22T09:00:00.000Z",
            "last_edited_time": "2026-08-22T09:00:00.000Z",
            "properties": response_properties,
        }

    async def update_page(
        self, page_id: str, properties: Mapping[str, Any]
    ) -> NotionObject:
        call = {"page_id": page_id, "properties": dict(properties)}
        self.updated.append(call)
        return {"id": page_id, "properties": dict(properties)}

    async def upload_file(
        self,
        path: Path,
        *,
        filename: str | None = None,
        content_type: str = "application/octet-stream",
    ) -> str:
        self.uploaded.append(
            {
                "path": path,
                "filename": filename,
                "content_type": content_type,
            }
        )
        return f"upload-{len(self.uploaded)}"


def expense_page(
    page_id: str,
    *,
    description: str,
    date_: str,
    category: str,
    subcategory: str,
    final: float,
) -> NotionObject:
    """Build the relevant portion of a Notion expense page response."""

    return {
        "id": page_id,
        "properties": {
            "Description": {
                "type": "title",
                "title": [{"plain_text": description}],
            },
            "Date": {"type": "date", "date": {"start": date_}},
            "Category": {
                "type": "multi_select",
                "multi_select": [{"name": category}],
            },
            "Sub Category": {
                "type": "multi_select",
                "multi_select": [{"name": subcategory}],
            },
            "Payment Method": {"type": "select", "select": {"name": "Credit card"}},
            "Final": {
                "type": "formula",
                "formula": {"type": "number", "number": final},
            },
        },
    }


def budget_page(
    page_id: str,
    *,
    name: str,
    amount: float,
    progressive: str | None = None,
    volatility: float | None = None,
) -> NotionObject:
    """Build a Notion budget page response."""

    properties: dict[str, Any] = {
        "Name": {"type": "title", "title": [{"plain_text": name}]},
        "Date": {"type": "date", "date": {"start": "2026-08-01"}},
        "Budget": {"type": "number", "number": amount},
    }
    if progressive is not None:
        properties["Progressive"] = {
            "type": "select",
            "select": {"name": progressive},
        }
    if volatility is not None:
        properties["Volatility"] = {"type": "number", "number": volatility}
    return {
        "id": page_id,
        "properties": properties,
    }


class FakeFinanceReader:
    """FinanceReader backed by plain objects for service and tool tests."""

    def __init__(
        self,
        *,
        transactions: Sequence[Transaction] = (),
        incomes: Sequence[Income] = (),
        budgets: Sequence[Budget] = (),
        planned: Sequence[PlannedExpense] = (),
    ) -> None:
        self.transaction_rows = tuple(transactions)
        self.income_rows = tuple(incomes)
        self.budget_rows = tuple(budgets)
        self.planned_rows = tuple(planned)

    async def transaction(self, page_id: str) -> Transaction | None:
        return next(
            (item for item in self.transaction_rows if item.id == page_id),
            None,
        )

    async def transactions(
        self, start_date: date, end_date: date, *, limit: int | None = None
    ) -> tuple[Transaction, ...]:
        rows = tuple(
            item for item in self.transaction_rows if start_date <= item.occurred_on <= end_date
        )
        return rows[:limit] if limit is not None else rows

    async def incomes(self, start_date: date, end_date: date) -> tuple[Income, ...]:
        return tuple(
            item for item in self.income_rows if start_date <= item.received_on <= end_date
        )

    async def budgets(self, month: date) -> tuple[Budget, ...]:
        target = month.replace(day=1)
        return tuple(item for item in self.budget_rows if item.month == target)

    async def planned_expenses(
        self, start_date: date, end_date: date, *, limit: int | None = None
    ) -> tuple[PlannedExpense, ...]:
        rows = tuple(
            item for item in self.planned_rows if start_date <= item.due_date <= end_date
        )
        return rows[:limit] if limit is not None else rows
