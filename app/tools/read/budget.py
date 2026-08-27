"""Grounded read tool for agent-directed monthly budget planning."""

from langchain_core.tools import BaseTool, tool

from app.services.budget_planning import BudgetPlanningService
from app.tools.read.finance import MonthInput, _month
from app.tools.serialization import JsonValue, jsonable


def build_budget_read_tools(service: BudgetPlanningService) -> list[BaseTool]:
    @tool("get_budget_planning_context", args_schema=MonthInput)
    async def get_budget_planning_context(month: str) -> JsonValue:
        """Load target/prior budgets, income, future needs, and approved planning rules."""

        return jsonable(await service.planning_context(_month(month)))

    return [get_budget_planning_context]
