"""Read-only access to durable financial profile context."""

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.domain.interaction import is_interaction_entry
from app.domain.operational_context import is_operational_context_entry
from app.services.profile import FinancialProfileService
from app.tools.serialization import JsonValue, jsonable


class ProfileReadInput(BaseModel):
    scope: str | None = Field(
        default=None,
        description="Optional exact Scope value, such as Budgeting or Savings.",
    )
    limit: int = Field(default=100, ge=1, le=100)


def build_profile_read_tools(service: FinancialProfileService) -> list[BaseTool]:
    @tool("get_financial_profile", args_schema=ProfileReadInput)
    async def get_financial_profile(
        scope: str | None = None, limit: int = 100
    ) -> JsonValue:
        """Return Active financial preferences, decisions, constraints, and goals."""

        entries = await service.active_entries(limit=limit)
        entries = tuple(
            entry
            for entry in entries
            if not is_interaction_entry(entry)
            and not is_operational_context_entry(entry)
        )
        if scope is not None:
            target = scope.strip().casefold()
            entries = tuple(
                entry
                for entry in entries
                if any(item.casefold() == target for item in entry.scopes)
            )
        return jsonable(entries)

    return [get_financial_profile]
