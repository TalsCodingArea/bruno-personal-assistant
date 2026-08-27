"""Approval-interrupted tools that mutate the durable financial profile."""

from datetime import datetime
from typing import Any, Literal

from langchain_core.tools import BaseTool, tool
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app.domain.interaction import is_interaction_key
from app.domain.profile import FinancialProfileUpdateDraft, ProfileKind
from app.services.profile import FinancialProfileService
from app.tools.serialization import JsonValue, jsonable
from app.tools.write.approval import is_approved


class ProfileApplyInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    key: str = Field(pattern=r"^[a-z0-9]+([._-][a-z0-9]+)*$")
    kind: Literal["Preference", "Decision", "Constraint", "Goal"]
    scopes: tuple[str, ...] = Field(default=())
    statement: str = Field(min_length=1, max_length=1800)
    rationale: str = Field(min_length=1, max_length=1800)
    current_page_id: str | None = None
    current_statement: str | None = None
    current_last_edited_at: datetime | None = None


def build_profile_write_tools(service: FinancialProfileService) -> list[BaseTool]:
    @tool("apply_financial_profile_update", args_schema=ProfileApplyInput)
    async def apply_financial_profile_update(
        name: str,
        key: str,
        kind: str,
        statement: str,
        rationale: str,
        scopes: tuple[str, ...] = (),
        current_page_id: str | None = None,
        current_statement: str | None = None,
        current_last_edited_at: datetime | None = None,
    ) -> JsonValue:
        """Pause for approval, then create a new Active profile version in Notion."""

        if is_interaction_key(key.strip().casefold()):
            raise ValueError(
                "assistant.* keys must use apply_interaction_preference_update"
            )
        draft = FinancialProfileUpdateDraft(
            name=name.strip(),
            key=key.strip().casefold(),
            kind=ProfileKind(kind),
            scopes=tuple(scopes),
            statement=statement.strip(),
            rationale=rationale.strip(),
            current_page_id=current_page_id,
            current_statement=current_statement,
            current_last_edited_at=current_last_edited_at,
        )
        decision: Any = interrupt(
            {
                "type": "financial_profile_write_approval",
                "allowed_actions": ["approve", "reject"],
                "message": "Approve creating this Financial Rules version in Notion?",
                "proposal": jsonable(draft),
            }
        )
        if not is_approved(decision):
            return {
                "status": "rejected",
                "message": "The proposed financial profile update was not written.",
            }
        result = await service.apply_update(draft)
        return {"status": "applied", "result": jsonable(result)}

    return [apply_financial_profile_update]
