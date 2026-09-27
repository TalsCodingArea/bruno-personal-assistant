"""Shared OpenAI-compatible inputs for interaction-profile tools."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

InteractionSettingInput = Literal[
    "tone",
    "banter",
    "verbosity",
    "coaching_style",
    "proactivity",
    "language",
]
InteractionValueInput = Literal[
    "warm",
    "neutral",
    "professional",
    "casual",
    "none",
    "light",
    "playful",
    "concise",
    "balanced",
    "detailed",
    "gentle",
    "direct",
    "challenging",
    "reactive",
    "proactive",
    "match_user",
    "english",
    "hebrew",
]


class InteractionDraftInput(BaseModel):
    setting: InteractionSettingInput
    value: InteractionValueInput = Field(
        description=(
            "Allowed pairs: tone=warm|neutral|professional|casual; "
            "banter=none|light|playful; verbosity=concise|balanced|detailed; "
            "coaching_style=gentle|direct|challenging; "
            "proactivity=reactive|balanced|proactive; "
            "language=match_user|english|hebrew."
        )
    )
    rationale: str = Field(min_length=1, max_length=1800)


class InteractionApplyInput(InteractionDraftInput):
    current_page_id: str | None = None
    current_statement: str | None = None
    current_last_edited_at: datetime | None = None
