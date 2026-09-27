"""Tool catalog for the expandable general operations agent."""

from typing import Protocol

from financial_agent.domain.operational_context import OperationalContextRepair
from financial_agent.tools.serialization import JsonValue, jsonable
from langchain_core.tools import BaseTool, tool


class OperationalContextRepairer(Protocol):
    async def repair_duplicate_current_versions(
        self,
    ) -> tuple[OperationalContextRepair, ...]: ...


def build_general_tools(repairer: OperationalContextRepairer) -> tuple[BaseTool, ...]:
    """Expose narrow maintenance actions; add future general capabilities here."""

    @tool("repair_duplicate_operational_context_versions")
    async def repair_duplicate_operational_context_versions() -> JsonValue:
        """Repair duplicate Active operational-context versions and report confirmed changes."""

        repairs = await repairer.repair_duplicate_current_versions()
        return {
            "status": "repaired" if repairs else "healthy",
            "repair_count": len(repairs),
            "repairs": jsonable(repairs),
        }

    return (repair_duplicate_operational_context_versions,)
