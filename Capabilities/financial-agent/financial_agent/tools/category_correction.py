"""Conversation tools for explicit user category corrections."""

from datetime import date

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from financial_agent.services.category_correction import CategoryCorrectionService
from financial_agent.tools.serialization import JsonValue, jsonable


class CorrectionContextInput(BaseModel):
    start_date: date
    end_date: date


class CategoryCorrectionInput(BaseModel):
    page_id: str = Field(min_length=1, description="Exact expense ID from correction context.")
    category: str = Field(min_length=1, description="Exact grounded Notion category name.")
    subcategory: str = Field(min_length=1, description="Exact grounded Notion subcategory name.")
    expected_category: list[str] = Field(description="Current category_options from context.")
    expected_subcategory: list[str] = Field(description="Current subcategory_options from context.")
    remember: bool = Field(
        description="True only for a reusable merchant correction; false for one-time exceptions."
    )
    rationale: str = Field(
        min_length=1, max_length=1800, description="User's correction and scope."
    )


def build_category_correction_tools(service: CategoryCorrectionService) -> list[BaseTool]:
    @tool("get_expense_category_context", args_schema=CorrectionContextInput)
    async def get_expense_category_context(start_date: date, end_date: date) -> JsonValue:
        """Find expense IDs, current labels, and existing category pairs before correcting."""
        return jsonable(await service.context(start_date, end_date))

    @tool("correct_expense_category", args_schema=CategoryCorrectionInput)
    async def correct_expense_category(
        page_id: str,
        category: str,
        subcategory: str,
        expected_category: list[str],
        expected_subcategory: list[str],
        remember: bool,
        rationale: str,
    ) -> JsonValue:
        """Apply a user-requested category/subcategory correction and optionally remember it.

        Only use after the user asks to correct this expense. No extra approval is required.
        Resolve ambiguous expenses with the user first. Use existing exact category names;
        never invent labels. Remember only when the user's correction supports a merchant-wide
        rule, never for a one-time exception or a merchant with mixed purchase categories.
        Report applied and remembered separately, including any memory_error.
        """
        return jsonable(
            await service.correct(
                page_id=page_id,
                category=category,
                subcategory=subcategory,
                expected_category=tuple(expected_category),
                expected_subcategory=tuple(expected_subcategory),
                remember=remember,
                rationale=rationale,
            )
        )

    return [get_expense_category_context, correct_expense_category]
