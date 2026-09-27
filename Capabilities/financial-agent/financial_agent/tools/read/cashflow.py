"""Agent-facing read tools for bank history and settlement projections."""

from datetime import date

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from financial_agent.services.cashflow import CashflowService
from financial_agent.tools.read.finance import MonthInput, _month
from financial_agent.tools.serialization import JsonValue, jsonable


class BankMovementInput(BaseModel):
    start_date: date
    end_date: date
    limit: int = Field(default=50, ge=1, le=100)


class AccountOutlookInput(MonthInput):
    as_of: date


def build_cashflow_read_tools(service: CashflowService) -> list[BaseTool]:
    @tool("get_bank_movements", args_schema=BankMovementInput)
    async def get_bank_movements(
        start_date: date, end_date: date, limit: int = 50
    ) -> JsonValue:
        """Read bounded Bank Movement rows, including each row's resulting balance."""

        return jsonable(
            await service.bank_movements(start_date, end_date, limit=limit)
        )

    @tool("get_account_outlook", args_schema=AccountOutlookInput)
    async def get_account_outlook(month: str, as_of: date) -> JsonValue:
        """Project the next balance from salary, rent, card, and reimbursement facts."""

        return jsonable(await service.account_outlook(_month(month), as_of))

    return [get_bank_movements, get_account_outlook]
