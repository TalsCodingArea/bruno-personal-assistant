"""Side-effect-free drafts for interaction-profile changes."""

from langchain_core.tools import BaseTool, tool

from financial_agent.domain.interaction import InteractionSetting
from financial_agent.services.interaction import InteractionProfileService
from financial_agent.tools.interaction_input import InteractionDraftInput
from financial_agent.tools.serialization import JsonValue, jsonable


def build_interaction_draft_tools(service: InteractionProfileService) -> list[BaseTool]:
    @tool("draft_interaction_preference_update", args_schema=InteractionDraftInput)
    async def draft_interaction_preference_update(
        setting: str,
        value: str,
        rationale: str,
    ) -> JsonValue:
        """Draft one validated conversation-style preference; write nothing."""

        return jsonable(
            await service.draft_update(
                setting=InteractionSetting(setting),
                value=value,
                rationale=rationale,
            )
        )

    return [draft_interaction_preference_update]
