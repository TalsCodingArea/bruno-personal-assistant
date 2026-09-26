"""Draft tools return proposals and never write to Notion."""

from datetime import date

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from financial_agent.services.finance_queries import FinanceQueryService
from financial_agent.tools.money_input import CurrencyText, parse_currency_text
from financial_agent.tools.read.finance import MonthInput, _month
from financial_agent.tools.serialization import JsonValue, jsonable


class PlannedExpenseDraftInput(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    target_amount: CurrencyText = Field(
        description="Positive target amount as a base-10 currency string."
    )
    due_date: date
    today: date
    saved_amount: CurrencyText = Field(
        default="0.00",
        description="Non-negative amount already saved as a currency string.",
    )


def build_draft_tools(service: FinanceQueryService) -> list[BaseTool]:
    """Build proposal-only tools with no integration write dependency."""

    @tool("draft_category_updates", args_schema=MonthInput)
    async def draft_category_updates(month: str) -> JsonValue:
        """Draft grounded category updates; do not alter any transaction."""

        return jsonable(await service.category_update_drafts(_month(month)))

    @tool("draft_planned_expense", args_schema=PlannedExpenseDraftInput)
    def draft_planned_expense(
        name: str,
        target_amount: str,
        due_date: date,
        today: date,
        saved_amount: str = "0.00",
    ) -> JsonValue:
        """Draft a planned expense and its allocation; do not create a Notion page."""

        return jsonable(
            service.planned_expense_draft(
                name,
                parse_currency_text(
                    target_amount,
                    field_name="target_amount",
                    positive=True,
                ),
                due_date,
                today,
                saved_amount=parse_currency_text(
                    saved_amount,
                    field_name="saved_amount",
                    non_negative=True,
                ),
            )
        )

    return [draft_category_updates, draft_planned_expense]
