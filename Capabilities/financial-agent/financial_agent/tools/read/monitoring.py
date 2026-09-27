"""Read-only access to grounded expense-monitoring decisions."""

from datetime import date

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from financial_agent.services.ports import ExpenseMonitorLedger
from financial_agent.tools.serialization import JsonValue, jsonable


class MonitoringDecisionInput(BaseModel):
    event_id: str | None = Field(
        default=None,
        description="Optional exact expense-trigger event ID.",
    )
    notion_page_id: str | None = Field(
        default=None,
        description="Optional exact Notion expense page ID.",
    )
    month: str | None = Field(
        default=None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
        description="Optional affected month in YYYY-MM format.",
    )
    limit: int = Field(default=10, ge=1, le=50)


def build_monitoring_read_tools(ledger: ExpenseMonitorLedger) -> list[BaseTool]:
    @tool("get_expense_monitoring_decisions", args_schema=MonitoringDecisionInput)
    async def get_expense_monitoring_decisions(
        event_id: str | None = None,
        notion_page_id: str | None = None,
        month: str | None = None,
        limit: int = 10,
    ) -> JsonValue:
        """Retrieve grounded monitor decisions for alert review or correction."""

        parsed_month = date.fromisoformat(f"{month}-01") if month is not None else None
        decisions = await ledger.decisions(
            event_id=event_id.strip() if event_id is not None else None,
            page_id=(
                notion_page_id.strip() if notion_page_id is not None else None
            ),
            month=parsed_month,
            limit=limit,
        )
        return jsonable(decisions)

    return [get_expense_monitoring_decisions]
