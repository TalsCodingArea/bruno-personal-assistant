"""Map Notion finance pages into plain domain objects."""

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from financial_agent.domain.models import (
    Budget,
    Income,
    PlannedExpense,
    ProgressiveMode,
    Transaction,
)
from financial_agent.domain.money import ZERO, money_from_api
from financial_agent.integrations.notion import NotionGateway
from financial_agent.integrations.notion_schema import (
    FinanceDataSources,
    FinanceProperties,
    PlannedExpenseProperties,
)


class PlannedExpenseSchemaNotConfigured(NotImplementedError):
    """Future Expenses cannot be mapped until its property contract is known."""


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


def _options(page: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = _property(page, name)
    prop_type = value.get("type")
    raw_options: list[object]
    if prop_type in {"select", "status"}:
        option = value.get(prop_type)
        raw_options = [option] if isinstance(option, dict) else []
    elif prop_type == "multi_select":
        items = value.get("multi_select")
        raw_options = items if isinstance(items, list) else []
    else:
        raw_options = []
    names = (
        item.get("name", "").strip()
        for item in raw_options
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    )
    return tuple(dict.fromkeys(name for name in names if name))


def _option(page: Mapping[str, Any], name: str) -> str | None:
    """Return a canonical option only when the property has exactly one value."""

    options = _options(page, name)
    return options[0] if len(options) == 1 else None


def _number(page: Mapping[str, Any], name: str) -> Decimal | None:
    value = _property(page, name)
    prop_type = value.get("type")
    raw: object = None
    if prop_type == "number":
        raw = value.get("number")
    elif prop_type in {"formula", "rollup"}:
        calculated = value.get(prop_type)
        if isinstance(calculated, dict) and calculated.get("type") == "number":
            raw = calculated.get("number")
    return None if raw is None else money_from_api(raw)


def _decimal_number(page: Mapping[str, Any], name: str) -> Decimal | None:
    value = _property(page, name)
    raw: object = value.get("number") if value.get("type") == "number" else None
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float | str | Decimal):
        raise TypeError(f"Unsupported decimal value: {raw!r}")
    return Decimal(str(raw))


def _date(page: Mapping[str, Any], name: str) -> date | None:
    value = _property(page, name).get("date")
    start = value.get("start") if isinstance(value, dict) else None
    if not isinstance(start, str):
        return None
    return date.fromisoformat(start[:10])


def _datetime_property(page: Mapping[str, Any], name: str) -> datetime | None:
    value = _property(page, name).get("date")
    start = value.get("start") if isinstance(value, dict) else None
    if not isinstance(start, str):
        return None
    parsed = start if "T" in start else f"{start}T00:00:00+00:00"
    return datetime.fromisoformat(parsed.replace("Z", "+00:00"))


def _page_timestamp(page: Mapping[str, Any], name: str) -> datetime | None:
    value = page.get(name)
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _progressive_mode(page: Mapping[str, Any], name: str) -> ProgressiveMode | None:
    selected = _option(page, name)
    if selected is None:
        return None
    normalized = selected.strip().casefold()
    for mode in ProgressiveMode:
        if mode.value.casefold() == normalized:
            return mode
    raise ValueError(f"Unsupported Progressive value: {selected!r}")


def _page_id(page: Mapping[str, Any]) -> str:
    value = page.get("id")
    if not isinstance(value, str):
        raise ValueError("Notion page is missing an ID")
    return value


def _date_filter(property_name: str, start_date: date, end_date: date) -> dict[str, Any]:
    return {
        "and": [
            {"property": property_name, "date": {"on_or_after": start_date.isoformat()}},
            {"property": property_name, "date": {"on_or_before": end_date.isoformat()}},
        ]
    }


class NotionFinanceReader:
    """Read finance rows through Notion and map them at the integration boundary."""

    def __init__(
        self,
        notion: NotionGateway,
        *,
        sources: FinanceDataSources | None = None,
        properties: FinanceProperties | None = None,
        planned_properties: PlannedExpenseProperties | None = None,
    ) -> None:
        self.notion = notion
        if sources is None:
            raise ValueError("FinanceDataSources must be supplied from runtime settings")
        self.sources = sources
        self.props = properties or FinanceProperties()
        self.planned_props = planned_properties or PlannedExpenseProperties()

    async def transaction(self, page_id: str) -> Transaction | None:
        """Retrieve the exact expense page named by a trusted event trigger."""

        page = await self.notion.retrieve_page(page_id)
        if page.get("archived") is True or page.get("in_trash") is True:
            return None
        return self._transaction(page)

    async def transactions(
        self, start_date: date, end_date: date, *, limit: int | None = None
    ) -> tuple[Transaction, ...]:
        filter_ = _date_filter(self.props.expense_date, start_date, end_date)
        filter_["and"].append(
            {
                "property": self.props.expense_tag,
                "multi_select": {"contains": self.props.owner_tag},
            }
        )
        pages = await self.notion.query_all(
            self.sources.expenses,
            filter_=filter_,
            sorts=[{"property": self.props.expense_date, "direction": "descending"}],
            filter_properties=[
                self.props.expense_description,
                self.props.expense_date,
                self.props.expense_category,
                self.props.expense_subcategory,
                self.props.expense_payment_type,
                self.props.expense_final,
            ],
            max_results=limit,
        )
        transactions: list[Transaction] = []
        for page in pages:
            transaction = self._transaction(page)
            if transaction is not None:
                transactions.append(transaction)
        return tuple(transactions)

    def _transaction(self, page: Mapping[str, Any]) -> Transaction | None:
        occurred_on = _date(page, self.props.expense_date)
        if occurred_on is None:
            return None
        return Transaction(
            id=_page_id(page),
            description=_text(page, self.props.expense_description),
            occurred_on=occurred_on,
            final_amount=_number(page, self.props.expense_final) or ZERO,
            category_options=_options(page, self.props.expense_category),
            subcategory_options=_options(page, self.props.expense_subcategory),
            payment_type=_option(page, self.props.expense_payment_type),
        )

    async def incomes(self, start_date: date, end_date: date) -> tuple[Income, ...]:
        pages = await self.notion.query_all(
            self.sources.income,
            filter_=_date_filter(self.props.income_date, start_date, end_date),
            filter_properties=[
                self.props.income_name,
                self.props.income_amount,
                self.props.income_date,
            ],
        )
        incomes: list[Income] = []
        for page in pages:
            received_on = _date(page, self.props.income_date)
            amount = _number(page, self.props.income_amount)
            if received_on is None or amount is None:
                continue
            incomes.append(
                Income(
                    id=_page_id(page),
                    name=_text(page, self.props.income_name),
                    received_on=received_on,
                    amount=amount,
                )
            )
        return tuple(incomes)

    async def budgets(self, month: date) -> tuple[Budget, ...]:
        start = month.replace(day=1)
        if start.month == 12:
            next_month = date(start.year + 1, 1, 1)
        else:
            next_month = date(start.year, start.month + 1, 1)
        end = date.fromordinal(next_month.toordinal() - 1)
        pages = await self.notion.query_all(
            self.sources.budgets,
            filter_=_date_filter(self.props.budget_date, start, end),
        )
        budgets: list[Budget] = []
        for page in pages:
            budget_month = _date(page, self.props.budget_date)
            amount = _number(page, self.props.budget_amount)
            if budget_month is None or amount is None:
                continue
            volatility = _decimal_number(page, self.props.budget_volatility)
            budgets.append(
                Budget(
                    id=_page_id(page),
                    subcategory=_text(page, self.props.budget_name),
                    month=budget_month,
                    amount=amount,
                    progressive=_progressive_mode(page, self.props.budget_progressive),
                    volatility_percent=(
                        volatility * Decimal(100) if volatility is not None else None
                    ),
                    baseline_amount=_number(page, self.props.budget_baseline),
                    last_adjustment_id=(
                        _text(page, self.props.budget_last_adjustment_id) or None
                    ),
                    last_adjustment_reason=(
                        _text(page, self.props.budget_last_adjustment_reason) or None
                    ),
                    last_adjustment_at=_datetime_property(
                        page, self.props.budget_last_adjustment_at
                    ),
                    last_edited_at=_page_timestamp(page, "last_edited_time"),
                )
            )
        return tuple(budgets)

    async def planned_expenses(
        self, start_date: date, end_date: date, *, limit: int | None = None
    ) -> tuple[PlannedExpense, ...]:
        names = self.planned_props
        if not all((names.name, names.target_amount, names.due_date)):
            raise PlannedExpenseSchemaNotConfigured(
                "Future Expenses needs name, target amount, and due date property mappings."
            )
        assert names.name is not None
        assert names.target_amount is not None
        assert names.due_date is not None
        filter_properties = [names.name, names.target_amount, names.due_date]
        if names.saved_amount:
            filter_properties.append(names.saved_amount)
        pages = await self.notion.query_all(
            self.sources.future_expenses,
            filter_=_date_filter(names.due_date, start_date, end_date),
            sorts=[{"property": names.due_date, "direction": "ascending"}],
            filter_properties=filter_properties,
            max_results=limit,
        )
        planned: list[PlannedExpense] = []
        for page in pages:
            due_date = _date(page, names.due_date)
            target = _number(page, names.target_amount)
            if due_date is None or target is None:
                continue
            saved = _number(page, names.saved_amount) if names.saved_amount else ZERO
            planned.append(
                PlannedExpense(
                    id=_page_id(page),
                    name=_text(page, names.name),
                    target_amount=target,
                    due_date=due_date,
                    saved_amount=saved or ZERO,
                )
            )
        return tuple(planned)
