"""Guarded, idempotent Notion writer for verified budget mutations."""

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from financial_agent.domain.budget_mutation import (
    AppliedBudgetMutationItem,
    BudgetMutationApplyError,
    BudgetMutationFreshnessError,
    BudgetMutationItem,
    BudgetMutationPartialFailure,
    BudgetMutationResult,
    BudgetMutationStatus,
    VerifiedBudgetMutation,
)
from financial_agent.domain.money import money_from_api
from financial_agent.integrations.notion import NotionGateway
from financial_agent.integrations.notion_schema import FinanceProperties


class NotionBudgetMutationRepository:
    """Preflight every page, write sequentially, then verify or roll back."""

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

    async def apply(self, mutation: VerifiedBudgetMutation) -> BudgetMutationResult:
        await self._validate_schema()
        proposal = mutation.proposal
        page_by_id: dict[str, Mapping[str, Any]] = {}
        already_applied: set[str] = set()
        for item in proposal.items:
            page = await self.notion.retrieve_page(item.page_id)
            page_by_id[item.page_id] = page
            if self._is_already_applied(page, item, proposal.operation_id):
                already_applied.add(item.page_id)
            else:
                self._verify_before(page, item)

        completed: list[str] = []
        adjusted_at = self.clock()
        try:
            for item in proposal.items:
                if item.page_id in already_applied:
                    continue
                await self.notion.update_page(
                    item.page_id,
                    self._apply_properties(
                        item,
                        operation_id=proposal.operation_id,
                        reason=proposal.reason,
                        adjusted_at=adjusted_at,
                    ),
                )
                completed.append(item.page_id)
            for item in proposal.items:
                page = await self.notion.retrieve_page(item.page_id)
                if not self._is_already_applied(page, item, proposal.operation_id):
                    raise BudgetMutationApplyError(
                        f"Postflight verification failed for {item.subcategory!r}"
                    )
        except Exception as exc:
            rollback_failures = await self._rollback(
                proposal.items,
                completed,
                proposal.operation_id,
            )
            if rollback_failures:
                names = ", ".join(rollback_failures)
                raise BudgetMutationPartialFailure(
                    f"Budget mutation failed and rollback was incomplete for: {names}"
                ) from exc
            raise BudgetMutationApplyError(
                "Budget mutation failed; completed updates were rolled back"
            ) from exc

        results = tuple(
            AppliedBudgetMutationItem(
                page_id=item.page_id,
                subcategory=item.subcategory,
                amount_before=item.amount_before,
                amount_after=item.amount_after,
                baseline_amount=_baseline(item),
                already_applied=item.page_id in already_applied,
            )
            for item in proposal.items
        )
        status = (
            BudgetMutationStatus.ALREADY_APPLIED
            if len(already_applied) == len(proposal.items)
            else BudgetMutationStatus.APPLIED
        )
        return BudgetMutationResult(
            status=status,
            operation_id=proposal.operation_id,
            items=results,
        )

    def _verify_before(
        self, page: Mapping[str, Any], item: BudgetMutationItem
    ) -> None:
        if page.get("id") != item.page_id:
            raise BudgetMutationFreshnessError("Notion returned the wrong budget page")
        if item.last_edited_at_before is None:
            raise BudgetMutationFreshnessError(
                f"Budget {item.subcategory!r} lacks a Notion version timestamp"
            )
        if self._timestamp(page, "last_edited_time") != item.last_edited_at_before:
            raise BudgetMutationFreshnessError(
                f"Budget {item.subcategory!r} was edited after analysis"
            )
        progressive = self._option(page, self.props.budget_progressive)
        volatility = self._raw_number(page, self.props.budget_volatility)
        volatility_percent = (
            volatility * Decimal("100") if volatility is not None else None
        )
        if (
            self._text(page, self.props.budget_name) != item.subcategory
            or self._date(page, self.props.budget_date) != item.month
            or self._number(page, self.props.budget_amount) != item.amount_before
            or (progressive.casefold() if progressive is not None else None)
            != (
                item.progressive.value.casefold()
                if item.progressive is not None
                else None
            )
            or volatility_percent != item.volatility_percent
            or self._number(page, self.props.budget_baseline) != item.baseline_before
            or (self._text(page, self.props.budget_last_adjustment_id) or None)
            != item.last_adjustment_id_before
            or (self._text(page, self.props.budget_last_adjustment_reason) or None)
            != item.last_adjustment_reason_before
            or self._date_time(page, self.props.budget_last_adjustment_at)
            != item.last_adjustment_at_before
        ):
            raise BudgetMutationFreshnessError(
                f"Budget {item.subcategory!r} properties changed after analysis"
            )

    def _is_already_applied(
        self, page: Mapping[str, Any], item: BudgetMutationItem, operation_id: str
    ) -> bool:
        return (
            self._number(page, self.props.budget_amount) == item.amount_after
            and self._text(page, self.props.budget_last_adjustment_id) == operation_id
            and self._number(page, self.props.budget_baseline) == _baseline(item)
        )

    def _apply_properties(
        self,
        item: BudgetMutationItem,
        *,
        operation_id: str,
        reason: str,
        adjusted_at: datetime,
    ) -> dict[str, Any]:
        return {
            self.props.budget_amount: {"number": _api_number(item.amount_after)},
            self.props.budget_baseline: {
                "number": _api_number(_baseline(item))
            },
            self.props.budget_last_adjustment_id: _rich_text(operation_id),
            self.props.budget_last_adjustment_reason: _rich_text(reason),
            self.props.budget_last_adjustment_at: {
                "date": {"start": adjusted_at.isoformat()}
            },
        }

    async def _rollback(
        self,
        items: tuple[BudgetMutationItem, ...],
        completed: list[str],
        operation_id: str,
    ) -> list[str]:
        failures: list[str] = []
        by_id = {item.page_id: item for item in items}
        for page_id in reversed(completed):
            item = by_id[page_id]
            try:
                current = await self.notion.retrieve_page(page_id)
                if not self._is_already_applied(current, item, operation_id):
                    failures.append(item.subcategory)
                    continue
                await self.notion.update_page(page_id, self._restore_properties(item))
            except Exception:
                failures.append(item.subcategory)
        return failures

    def _restore_properties(self, item: BudgetMutationItem) -> dict[str, Any]:
        return {
            self.props.budget_amount: {"number": _api_number(item.amount_before)},
            self.props.budget_baseline: {
                "number": (
                    _api_number(item.baseline_before)
                    if item.baseline_before is not None
                    else None
                )
            },
            self.props.budget_last_adjustment_id: _rich_text(
                item.last_adjustment_id_before or ""
            ),
            self.props.budget_last_adjustment_reason: _rich_text(
                item.last_adjustment_reason_before or ""
            ),
            self.props.budget_last_adjustment_at: {
                "date": (
                    {"start": item.last_adjustment_at_before.isoformat()}
                    if item.last_adjustment_at_before is not None
                    else None
                )
            },
        }

    @staticmethod
    def _properties(page: Mapping[str, Any]) -> Mapping[str, Any]:
        value = page.get("properties")
        return value if isinstance(value, dict) else {}

    def _property(self, page: Mapping[str, Any], name: str) -> Mapping[str, Any]:
        value = self._properties(page).get(name)
        return value if isinstance(value, dict) else {}

    def _text(self, page: Mapping[str, Any], name: str) -> str:
        value = self._property(page, name)
        prop_type = value.get("type")
        items = value.get(prop_type) if prop_type in {"title", "rich_text"} else []
        if not isinstance(items, list):
            return ""
        return "".join(
            item.get("plain_text", "")
            for item in items
            if isinstance(item, dict)
        ).strip()

    def _option(self, page: Mapping[str, Any], name: str) -> str | None:
        value = self._property(page, name)
        prop_type = value.get("type")
        option = value.get(prop_type) if prop_type in {"select", "status"} else None
        selected = option.get("name") if isinstance(option, dict) else None
        return selected if isinstance(selected, str) else None

    def _number(self, page: Mapping[str, Any], name: str) -> Decimal | None:
        value = self._property(page, name)
        raw = value.get("number") if value.get("type") == "number" else None
        return money_from_api(raw) if raw is not None else None

    def _raw_number(self, page: Mapping[str, Any], name: str) -> Decimal | None:
        value = self._property(page, name)
        raw = value.get("number") if value.get("type") == "number" else None
        if raw is None:
            return None
        if isinstance(raw, bool) or not isinstance(raw, int | float | str | Decimal):
            raise TypeError(f"Unsupported decimal value: {raw!r}")
        return Decimal(str(raw))

    def _date(self, page: Mapping[str, Any], name: str) -> date | None:
        value = self._property(page, name).get("date")
        start = value.get("start") if isinstance(value, dict) else None
        return date.fromisoformat(start[:10]) if isinstance(start, str) else None

    def _date_time(self, page: Mapping[str, Any], name: str) -> datetime | None:
        value = self._property(page, name).get("date")
        start = value.get("start") if isinstance(value, dict) else None
        if not isinstance(start, str):
            return None
        parsed = start if "T" in start else f"{start}T00:00:00+00:00"
        return datetime.fromisoformat(parsed.replace("Z", "+00:00"))

    @staticmethod
    def _timestamp(page: Mapping[str, Any], name: str) -> datetime | None:
        value = page.get(name)
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else None
        )

    async def _validate_schema(self) -> None:
        if self._schema_validated:
            return
        data_source = await self.notion.retrieve_data_source(self.data_source_id)
        properties = data_source.get("properties")
        if not isinstance(properties, dict):
            raise BudgetMutationFreshnessError(
                "Budget data source did not return a property schema"
            )
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
        invalid = [
            f"{name} ({expected})"
            for name, expected in required.items()
            if not isinstance(properties.get(name), dict)
            or properties[name].get("type") != expected
        ]
        if invalid:
            raise BudgetMutationFreshnessError(
                "Budget mutation schema is missing or invalid: " + ", ".join(invalid)
            )
        self._schema_validated = True


def _api_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(format(value, "f"))


def _baseline(item: BudgetMutationItem) -> Decimal:
    return (
        item.baseline_before
        if item.baseline_before is not None
        else item.amount_before
    )


def _rich_text(value: str) -> dict[str, list[dict[str, object]]]:
    if not value:
        return {"rich_text": []}
    return {"rich_text": [{"type": "text", "text": {"content": value}}]}
