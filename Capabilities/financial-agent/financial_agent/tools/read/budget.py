"""Grounded read tool for agent-directed monthly budget planning."""

from langchain_core.tools import BaseTool, tool

from financial_agent.services.budget_planning import BudgetPlanningService
from financial_agent.tools.read.finance import MonthInput, _month
from financial_agent.tools.serialization import JsonValue, jsonable


def build_budget_read_tools(service: BudgetPlanningService) -> list[BaseTool]:
    @tool("get_budget_planning_context", args_schema=MonthInput)
    async def get_budget_planning_context(month: str) -> JsonValue:
        """Load all current target-month Budget pages plus prior budgets and planning context."""

        return jsonable(await service.planning_context(_month(month)))

    return [get_budget_planning_context]
