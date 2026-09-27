"""Approval-gated recurring-task creation."""

from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from financial_agent.services.ports import RecurringTaskManager
from financial_agent.tools.read.recurring import _owner
from financial_agent.tools.serialization import JsonValue, jsonable
from financial_agent.tools.write.approval import is_approved


class RecurringTaskInput(BaseModel):
    prompt: str = Field(
        min_length=1,
        max_length=1800,
        description="Self-contained instruction Bruno should execute on every run.",
    )
    cron: str = Field(
        min_length=9,
        max_length=100,
        description="Standard five-field cron expression: minute hour day month weekday.",
    )
    timezone: str = Field(
        default="Asia/Jerusalem",
        min_length=1,
        max_length=100,
        description="IANA timezone name used to interpret the cron expression.",
    )


def build_recurring_write_tools(service: RecurringTaskManager) -> list[BaseTool]:
    @tool("create_recurring_task", args_schema=RecurringTaskInput)
    async def create_recurring_task(
        prompt: str,
        cron: str,
        timezone: str,
        config: RunnableConfig,
    ) -> JsonValue:
        """Pause for approval, then persist a recurring assistant task."""

        proposal = {
            "prompt": prompt.strip(),
            "cron": cron.strip(),
            "timezone": timezone.strip(),
        }
        decision: Any = interrupt(
            {
                "type": "recurring_task_write_approval",
                "allowed_actions": ["approve", "reject"],
                "message": "Approve creating this recurring task?",
                "proposal": proposal,
            }
        )
        if not is_approved(decision):
            return {"status": "rejected", "message": "Recurring task was not created."}
        return {
            "status": "applied",
            "result": jsonable(
                await service.create(
                    owner_thread_id=_owner(config),
                    prompt=proposal["prompt"],
                    cron=proposal["cron"],
                    timezone=proposal["timezone"],
                )
            ),
        }

    return [create_recurring_task]
