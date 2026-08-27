"""Approval-interrupted writes for interaction-profile changes."""

from datetime import datetime
from typing import Any

from langchain_core.tools import BaseTool, tool
from langgraph.types import interrupt

from app.domain.interaction import InteractionSetting
from app.services.interaction import InteractionProfileService
from app.tools.interaction_input import InteractionApplyInput
from app.tools.serialization import JsonValue, jsonable
from app.tools.write.approval import is_approved


def build_interaction_write_tools(service: InteractionProfileService) -> list[BaseTool]:
    @tool("apply_interaction_preference_update", args_schema=InteractionApplyInput)
    async def apply_interaction_preference_update(
        setting: str,
        value: str,
        rationale: str,
        current_page_id: str | None = None,
        current_statement: str | None = None,
        current_last_edited_at: datetime | None = None,
    ) -> JsonValue:
        """Pause for approval, then version one interaction preference in Notion."""

        draft = service.prepared_update(
            setting=InteractionSetting(setting),
            value=value,
            rationale=rationale,
            current_page_id=current_page_id,
            current_statement=current_statement,
            current_last_edited_at=current_last_edited_at,
        )
        decision: Any = interrupt(
            {
                "type": "interaction_profile_write_approval",
                "allowed_actions": ["approve", "reject"],
                "message": "Approve updating this interaction preference in Notion?",
                "proposal": jsonable(draft),
            }
        )
        if not is_approved(decision):
            return {
                "status": "rejected",
                "message": "The interaction preference was not written.",
            }
        result = await service.apply_update(draft)
        return {"status": "applied", "result": jsonable(result)}

    return [apply_interaction_preference_update]
