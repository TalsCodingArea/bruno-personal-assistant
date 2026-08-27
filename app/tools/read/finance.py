"""Narrow read-only tools for the first conversation graph."""

from datetime import date

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.integrations.notion_finance import PlannedExpenseSchemaNotConfigured
from app.services.finance_queries import FinanceQueryService
from app.tools.money_input import CurrencyText, parse_currency_text
from app.tools.serialization import JsonValue, jsonable


class MonthInput(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM")


class ForecastInput(MonthInput):
    as_of: date
    expected_income: CurrencyText | None = Field(
        default=None,
        description=(
            "Expected total income as a base-10 currency string, for example "
            "'12000.00'; omit to use income currently recorded in Notion."
        ),
    )
    planned_allocations: CurrencyText = Field(
        default="0.00",
        description="Non-negative planned allocations as a currency string.",
    )
    buffer: CurrencyText = Field(
        default="0.00",
        description="Non-negative emergency buffer as a currency string.",
    )


class UpcomingInput(BaseModel):
    today: date
    limit: int = Field(default=20, ge=1, le=50)


def _month(value: str) -> date:
    return date.fromisoformat(f"{value}-01")


def build_read_tools(service: FinanceQueryService) -> list[BaseTool]:
    """Build only tools that read and calculate without proposing mutations."""

    @tool("get_monthly_summary", args_schema=MonthInput)
    async def get_monthly_summary(month: str) -> JsonValue:
        """Return Final spending grouped and classified as regular or variable for a month."""

        return jsonable(await service.monthly_summary(_month(month)))

    @tool("get_uncategorized_transactions", args_schema=MonthInput)
    async def get_uncategorized_transactions(month: str) -> JsonValue:
        """Return transactions missing a category or sub-category for one month."""

        return jsonable(await service.uncategorized_transactions(_month(month)))

    @tool("suggest_categories", args_schema=MonthInput)
    async def suggest_categories(month: str) -> JsonValue:
        """Suggest categories only from exact merchant matches in categorized history."""

        return jsonable(await service.category_suggestions(_month(month)))

    @tool("get_budget_status", args_schema=MonthInput)
    async def get_budget_status(month: str) -> JsonValue:
        """Return regular budgets, variable spend, progress mode, and controllability."""

        return jsonable(await service.budget_status(_month(month)))

    @tool("forecast_month_end", args_schema=ForecastInput)
    async def forecast_month_end(
        month: str,
        as_of: date,
        expected_income: str | None = None,
        planned_allocations: str = "0.00",
        buffer: str = "0.00",
    ) -> JsonValue:
        """Forecast accumulated, discrete, and variable expenses with Decimal arithmetic."""

        income_amount = (
            parse_currency_text(
                expected_income,
                field_name="expected_income",
                non_negative=True,
            )
            if expected_income is not None
            else None
        )
        return jsonable(
            await service.forecast_month_end(
                _month(month),
                as_of,
                expected_income=income_amount,
                planned_allocations=parse_currency_text(
                    planned_allocations,
                    field_name="planned_allocations",
                    non_negative=True,
                ),
                buffer=parse_currency_text(
                    buffer,
                    field_name="buffer",
                    non_negative=True,
                ),
            )
        )

    @tool("get_upcoming_planned_expenses", args_schema=UpcomingInput)
    async def get_upcoming_planned_expenses(
        today: date, limit: int = 20
    ) -> JsonValue:
        """Return future expenses and deterministic allocations when its schema is configured."""

        try:
            return jsonable(await service.upcoming_planned_expenses(today, limit=limit))
        except PlannedExpenseSchemaNotConfigured as exc:
            return {"status": "schema_not_configured", "message": str(exc)}

    return [
        get_monthly_summary,
        get_uncategorized_transactions,
        suggest_categories,
        get_budget_status,
        forecast_month_end,
        get_upcoming_planned_expenses,
    ]
