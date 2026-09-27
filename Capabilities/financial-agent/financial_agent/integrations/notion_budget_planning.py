"""Notion writer for validated, idempotent monthly Budget page creation."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from financial_agent.domain.budget_planning import (
    BudgetPageDraft,
    BudgetPlanCreationResult,
    BudgetPlanPartialFailure,
    CreatedBudgetPage,
    MonthlyBudgetPlanDraft,
)
from financial_agent.domain.money import money_from_api
from financial_agent.integrations.notion import NotionGateway
from financial_agent.integrations.notion_schema import FinanceProperties


class NotionBudgetPlanCreationRepository:
    """Create only missing pages after service-level freshness verification."""

    def __init__(
        self,
        notion: NotionGateway,
        data_source_id: str,
        *,
        properties: FinanceProperties | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.notion = notion
        self.data_source_id = data_source_id
        self.props = properties or FinanceProperties()
        self.clock = clock or (lambda: datetime.now(UTC))
        self._schema_validated = False

    async def create(
        self,
        draft: MonthlyBudgetPlanDraft,
        already_created: tuple[CreatedBudgetPage, ...],
    ) -> BudgetPlanCreationResult:
        await self._validate_schema()
        completed = list(already_created)
        already_names = {
            item.subcategory.casefold() for item in already_created
        }
        created_at = self.clock()
        try:
            for item in draft.items:
                if item.subcategory.casefold() in already_names:
                    continue
                page = await self.notion.create_page(
                    self.data_source_id,
                    {
                        self.props.budget_name: _title(item.subcategory),
                        self.props.budget_date: {
                            "date": {"start": draft.target_month.isoformat()}
                        },
                        self.props.budget_amount: {
                            "number": _api_number(item.amount)
                        },
                        self.props.budget_progressive: {
                            "select": {"name": item.progressive.value}
                        },
                        self.props.budget_volatility: {
                            "number": _api_ratio(item.volatility_percent)
                        },
                        self.props.budget_baseline: {
                            "number": _api_number(item.amount)
                        },
                        self.props.budget_last_adjustment_id: _rich_text(
                            draft.operation_id
                        ),
                        self.props.budget_last_adjustment_reason: _rich_text(
                            _creation_reason(draft, item.subcategory)
                        ),
                        self.props.budget_last_adjustment_at: {
                            "date": {"start": created_at.isoformat()}
                        },
                    },
                )
                page_id = page.get("id")
                if not isinstance(page_id, str):
                    raise RuntimeError("Notion created a Budget page without an ID")
                _verify_created_page(
                    page,
                    draft,
                    item,
                    self.props,
                )
                completed.append(
                    CreatedBudgetPage(
                        page_id=page_id,
                        subcategory=item.subcategory,
                        amount=item.amount,
                        already_created=False,
                    )
                )
        except Exception as exc:
            names = ", ".join(item.subcategory for item in completed) or "none"
            raise BudgetPlanPartialFailure(
                "Budget page creation stopped after confirmed pages: "
                f"{names}. Retry the same approved draft to finish idempotently."
            ) from exc
        ordered = tuple(
            next(
                page
                for page in completed
                if page.subcategory.casefold() == item.subcategory.casefold()
            )
            for item in draft.items
        )
        return BudgetPlanCreationResult(draft.operation_id, ordered)

    async def _validate_schema(self) -> None:
        if self._schema_validated:
            return
        source = await self.notion.retrieve_data_source(self.data_source_id)
        raw = source.get("properties")
        properties = raw if isinstance(raw, Mapping) else {}
        required = {
            self.props.budget_name: "title",
            self.props.budget_date: "date",
            self.props.budget_amount: "number",
            self.props.budget_progressive: "select",
            self.props.budget_volatility: "number",
            self.props.budget_baseline: "number",
            self.props.budget_last_adjustment_id: "rich_text",
            self.props.budget_last_adjustment_reason: "rich_text",
            self.props.budget_last_adjustment_at: "date",
        }
        for name, expected_type in required.items():
            value = properties.get(name)
            actual = value.get("type") if isinstance(value, Mapping) else None
            if actual != expected_type:
                raise ValueError(
                    f"Budget data source property {name!r} must have type {expected_type!r}"
                )
        self._schema_validated = True


def _verify_created_page(
    page: Mapping[str, Any],
    draft: MonthlyBudgetPlanDraft,
    item: BudgetPageDraft,
    props: FinanceProperties,
) -> None:
    properties = page.get("properties")
    values = properties if isinstance(properties, Mapping) else {}
    if (
        _text(values.get(props.budget_name)) != item.subcategory
        or _date_text(values.get(props.budget_date)) != draft.target_month.isoformat()
        or _number(values.get(props.budget_amount)) != item.amount
        or _option(values.get(props.budget_progressive)) != item.progressive.value
        or _ratio_percent(values.get(props.budget_volatility))
        != item.volatility_percent
        or _number(values.get(props.budget_baseline)) != item.amount
        or _text(values.get(props.budget_last_adjustment_id)) != draft.operation_id
    ):
        raise RuntimeError(
            f"Could not verify created Budget page for {item.subcategory!r}"
        )


def _text(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    prop_type = value.get("type")
    items = value.get(prop_type) if prop_type in {"title", "rich_text"} else []
    if not isinstance(items, list):
        return ""
    return "".join(
        item.get("plain_text", "")
        for item in items
        if isinstance(item, Mapping)
    )


def _date_text(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    date_value = value.get("date")
    start = date_value.get("start") if isinstance(date_value, Mapping) else None
    return start if isinstance(start, str) else None


def _number(value: object) -> Decimal | None:
    if not isinstance(value, Mapping) or value.get("type") != "number":
        return None
    raw = value.get("number")
    return money_from_api(raw) if raw is not None else None


def _option(value: object) -> str | None:
    if not isinstance(value, Mapping) or value.get("type") != "select":
        return None
    selected = value.get("select")
    name = selected.get("name") if isinstance(selected, Mapping) else None
    return name if isinstance(name, str) else None


def _ratio_percent(value: object) -> Decimal | None:
    if not isinstance(value, Mapping) or value.get("type") != "number":
        return None
    raw = value.get("number")
    return money_from_api(raw) * Decimal("100") if raw is not None else None


def _title(value: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": value}}]}


def _rich_text(value: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": value}}]}


def _api_number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    return int(integral) if value == integral else float(value)


def _api_ratio(percent: Decimal) -> int | float:
    return _api_number(percent / Decimal("100"))


def _creation_reason(draft: MonthlyBudgetPlanDraft, subcategory: str) -> str:
    return (
        f"Validated {draft.target_month:%Y-%m} budget creation for {subcategory}; "
        f"cap basis={draft.cap_basis.value}; {draft.cap_rationale}"
    )[:1800]
