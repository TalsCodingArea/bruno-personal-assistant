"""Read-only recurring-task tool."""

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from financial_agent.services.ports import RecurringTaskManager
from financial_agent.tools.serialization import JsonValue, jsonable


def _owner(config: RunnableConfig) -> str:
    configurable = config.get("configurable", {})
    owner = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not isinstance(owner, str) or not owner:
        raise ValueError("Recurring tasks require a persistent conversation thread")
    return owner


def build_recurring_read_tools(service: RecurringTaskManager) -> list[BaseTool]:
    @tool("get_recurring_tasks")
    async def get_recurring_tasks(config: RunnableConfig) -> JsonValue:
        """List active recurring tasks owned by this conversation."""

        return jsonable(await service.list_for_owner(_owner(config)))

    return [get_recurring_tasks]
