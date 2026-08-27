"""Read-only access to the effective interaction profile."""

from langchain_core.tools import BaseTool, tool

from app.services.interaction import InteractionProfileService
from app.tools.serialization import JsonValue, jsonable


def build_interaction_read_tools(service: InteractionProfileService) -> list[BaseTool]:
    @tool("get_interaction_profile")
    async def get_interaction_profile() -> JsonValue:
        """Return effective tone, banter, verbosity, coaching, proactivity, and language."""

        return jsonable(await service.active_profile())

    return [get_interaction_profile]
