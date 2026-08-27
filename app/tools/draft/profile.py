"""Side-effect-free drafts for durable financial profile changes."""

from typing import Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.domain.interaction import is_interaction_key
from app.domain.profile import ProfileKind
from app.services.profile import FinancialProfileService
from app.tools.serialization import JsonValue, jsonable


class ProfileDraftInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    key: str = Field(
        pattern=r"^[a-z0-9]+([._-][a-z0-9]+)*$",
        description="Stable lowercase identifier, for example spending.groceries.split.",
    )
    kind: Literal["Preference", "Decision", "Constraint", "Goal"]
    scopes: tuple[str, ...] = Field(default=())
    statement: str = Field(min_length=1, max_length=1800)
    rationale: str = Field(min_length=1, max_length=1800)


def build_profile_draft_tools(service: FinancialProfileService) -> list[BaseTool]:
    @tool("draft_financial_profile_update", args_schema=ProfileDraftInput)
    async def draft_financial_profile_update(
        name: str,
        key: str,
        kind: str,
        statement: str,
        rationale: str,
        scopes: tuple[str, ...] = (),
    ) -> JsonValue:
        """Compare a proposed profile version with the Active Notion version; write nothing."""

        if is_interaction_key(key.strip().casefold()):
            raise ValueError(
                "assistant.* keys must use draft_interaction_preference_update"
            )
        return jsonable(
            await service.draft_update(
                name=name,
                key=key,
                kind=ProfileKind(kind),
                scopes=scopes,
                statement=statement,
                rationale=rationale,
            )
        )

    return [draft_financial_profile_update]
