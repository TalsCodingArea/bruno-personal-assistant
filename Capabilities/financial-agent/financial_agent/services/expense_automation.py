"""Trusted expense ingestion used by automations and receipt transports."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from datetime import timezone as datetime_timezone
from pathlib import Path
from typing import Any

from financial_agent.domain.models import Transaction
from financial_agent.domain.money import money
from financial_agent.integrations.notion import NotionGateway
from financial_agent.integrations.notion_schema import FinanceProperties
from financial_agent.services.ports import FinanceReader

DEFAULT_EXPENSE_TIMEZONE = "GMT+03:00"
_TIMEZONE_PATTERN = re.compile(r"^(?:GMT|UTC)?([+-])(\d{1,2})(?::?(\d{2}))?$")


@dataclass(frozen=True, slots=True)
class LoggedExpense:
    """Compact result returned after a trusted expense write."""

    page_id: str
    url: str | None
    description: str
    amount: str
    occurred_on: str


@dataclass(frozen=True, slots=True)
class ExpenseCandidate:
    """Existing same-day expense exposed to trusted duplicate matching."""

    page_id: str
    description: str
    amount: str
    occurred_on: str
    category: tuple[str, ...]
    subcategory: tuple[str, ...]
    payment_method: str | None


class ExpenseAutomationService:
    """Validate and create expenses without a conversational approval interrupt.

    This service is intentionally reachable only from trusted automation transports.
    Conversational writes continue to use the graph's approval-gated tool catalog.
    """

    def __init__(
        self,
        notion: NotionGateway,
        expenses_data_source_id: str,
        *,
        properties: FinanceProperties | None = None,
        reader: FinanceReader | None = None,
    ) -> None:
        self.notion = notion
        self.expenses_data_source_id = expenses_data_source_id
        self.properties = properties or FinanceProperties()
        self.reader = reader

    async def log_expense(
        self,
        *,
        description: str,
        amount: str,
        occurred_on: date | str | None = None,
        category: Sequence[str] = ("Uncategorized",),
        subcategory: Sequence[str] = (),
        payment_method: str = "Credit",
        expense_type: str = "Need",
        tags: Sequence[str] | None = None,
        invoice_path: Path | None = None,
        invoice_name: str | None = None,
        timezone: str = DEFAULT_EXPENSE_TIMEZONE,
    ) -> LoggedExpense:
        description_value = description.strip()
        if not description_value:
            raise ValueError("description cannot be empty")
        amount_value = money(amount)
        if amount_value <= 0:
            raise ValueError("amount must be positive")
        expense_date = self.expense_date(occurred_on, timezone=timezone)
        categories = _names(category, default=("Uncategorized",))
        subcategories = _names(subcategory)
        tag_names = _names(tags or (self.properties.owner_tag,))

        properties: dict[str, Any] = {
            self.properties.expense_description: _title(description_value),
            self.properties.expense_amount: {"number": float(amount_value)},
            self.properties.expense_date: {"date": {"start": expense_date.isoformat()}},
            self.properties.expense_category: _multi_select(categories),
            self.properties.expense_payment_type: _select(payment_method),
            self.properties.expense_type: _select(expense_type),
            self.properties.expense_tag: _multi_select(tag_names),
        }
        if subcategories:
            properties[self.properties.expense_subcategory] = _multi_select(subcategories)
        if invoice_path is not None:
            properties[self.properties.expense_invoice] = await self._invoice_property(
                invoice_path, invoice_name
            )

        page = await self.notion.create_page(self.expenses_data_source_id, properties)
        page_id = page.get("id")
        if not isinstance(page_id, str) or not page_id:
            raise RuntimeError("Notion created an expense without returning a page ID")
        page_url = page.get("url")
        return LoggedExpense(
            page_id=page_id,
            url=page_url if isinstance(page_url, str) else None,
            description=description_value,
            amount=str(amount_value),
            occurred_on=expense_date.isoformat(),
        )

    def expense_date(
        self,
        value: date | str | None,
        *,
        timezone: str = DEFAULT_EXPENSE_TIMEZONE,
    ) -> date:
        """Resolve an expense date in the caller-provided fixed UTC/GMT offset."""

        return _expense_date(value, timezone)

    async def expenses_on(
        self,
        occurred_on: date | str | None,
        *,
        timezone: str = DEFAULT_EXPENSE_TIMEZONE,
    ) -> tuple[ExpenseCandidate, ...]:
        """Read all trusted expense candidates from one resolved local date."""

        if self.reader is None:
            raise RuntimeError("Expense lookup requires a finance reader")
        target = self.expense_date(occurred_on, timezone=timezone)
        transactions = await self.reader.transactions(target, target)
        return tuple(_candidate(item) for item in transactions)

    async def attach_receipt(
        self,
        *,
        page_id: str,
        description: str,
        amount: str,
        occurred_on: date | str,
        invoice_path: Path,
        invoice_name: str | None = None,
    ) -> LoggedExpense:
        """Attach a PDF receipt without changing the existing expense's finance fields."""

        cleaned_page_id = page_id.strip()
        if not cleaned_page_id:
            raise ValueError("page_id cannot be empty")
        properties = {
            self.properties.expense_invoice: await self._invoice_property(
                invoice_path, invoice_name
            )
        }
        page = await self.notion.update_page(cleaned_page_id, properties)
        page_url = page.get("url")
        return LoggedExpense(
            page_id=cleaned_page_id,
            url=page_url if isinstance(page_url, str) else None,
            description=description,
            amount=str(money(amount)),
            occurred_on=self.expense_date(occurred_on).isoformat(),
        )

    async def _invoice_property(
        self, invoice_path: Path, invoice_name: str | None
    ) -> dict[str, object]:
        display_name = invoice_name or invoice_path.name
        upload_id = await self.notion.upload_file(
            invoice_path,
            filename=display_name,
            content_type="application/pdf",
        )
        return {
            "files": [
                {
                    "type": "file_upload",
                    "file_upload": {"id": upload_id},
                    "name": display_name,
                }
            ]
        }


def _expense_date(value: date | str | None, timezone_name: str) -> date:
    zone = _fixed_timezone(timezone_name)
    if value is None:
        return datetime.now(zone).date()
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value
    else:
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("occurred_on must be an ISO date or timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone).date()


def _fixed_timezone(value: str) -> tzinfo:
    normalized = value.strip().upper().replace(" ", "")
    match = _TIMEZONE_PATTERN.fullmatch(normalized)
    if match is None:
        raise ValueError("timezone must be a GMT/UTC offset such as GMT+03:00")
    sign, hours_text, minutes_text = match.groups()
    hours = int(hours_text)
    minutes = int(minutes_text or "0")
    if hours > 23 or minutes > 59:
        raise ValueError("timezone offset is out of range")
    offset = timedelta(hours=hours, minutes=minutes)
    if sign == "-":
        offset = -offset
    return datetime_timezone(offset)


def _candidate(transaction: Transaction) -> ExpenseCandidate:
    return ExpenseCandidate(
        page_id=transaction.id,
        description=transaction.description,
        amount=str(transaction.final_amount),
        occurred_on=transaction.occurred_on.isoformat(),
        category=transaction.category_options,
        subcategory=transaction.subcategory_options,
        payment_method=transaction.payment_type,
    )


def _names(values: Sequence[str], *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    result = tuple(value.strip() for value in values if value.strip())
    return result or default


def _title(value: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": value}}]}


def _select(value: str) -> dict[str, Any]:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("select values cannot be empty")
    return {"select": {"name": cleaned}}


def _multi_select(values: Sequence[str]) -> dict[str, Any]:
    return {"multi_select": [{"name": value} for value in values]}
