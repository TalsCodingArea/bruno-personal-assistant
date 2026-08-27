"""Compile and version the assistant's structured interaction profile."""

from datetime import datetime

from app.domain.interaction import (
    INTERACTION_DEFAULTS,
    INTERACTION_VALUE_TYPES,
    BanterLevel,
    CoachingStyle,
    InteractionProfile,
    InteractionSetting,
    LanguagePreference,
    Proactivity,
    Tone,
    Verbosity,
)
from app.domain.profile import (
    AppliedFinancialProfileUpdate,
    FinancialProfileEntry,
    FinancialProfileUpdateDraft,
    ProfileKind,
)
from app.services.profile import FinancialProfileService


class InvalidInteractionPreference(ValueError):
    """An interaction setting or stored value is outside its controlled vocabulary."""


def normalize_interaction_value(setting: InteractionSetting, value: str) -> str:
    """Validate one setting/value pair and return its canonical stored value."""

    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    value_type = INTERACTION_VALUE_TYPES[setting]
    try:
        return str(value_type(normalized))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in value_type)
        raise InvalidInteractionPreference(
            f"{setting.value} must be one of: {allowed}"
        ) from exc


def compile_interaction_profile(
    entries: tuple[FinancialProfileEntry, ...] | list[FinancialProfileEntry],
) -> InteractionProfile:
    """Overlay valid Active Notion entries on safe conversation defaults."""

    values: dict[InteractionSetting, str] = {
        InteractionSetting.TONE: INTERACTION_DEFAULTS.tone.value,
        InteractionSetting.BANTER: INTERACTION_DEFAULTS.banter.value,
        InteractionSetting.VERBOSITY: INTERACTION_DEFAULTS.verbosity.value,
        InteractionSetting.COACHING_STYLE: INTERACTION_DEFAULTS.coaching_style.value,
        InteractionSetting.PROACTIVITY: INTERACTION_DEFAULTS.proactivity.value,
        InteractionSetting.LANGUAGE: INTERACTION_DEFAULTS.language.value,
    }
    warnings: list[str] = []
    settings_by_key = {setting.key: setting for setting in InteractionSetting}
    for entry in entries:
        setting = settings_by_key.get(entry.key)
        if setting is None:
            if entry.key.startswith("assistant."):
                warnings.append(f"Ignored unknown interaction key: {entry.key}")
            continue
        try:
            normalized = normalize_interaction_value(setting, entry.statement)
            values[setting] = normalized
        except InvalidInteractionPreference as exc:
            warnings.append(f"Ignored {entry.key}: {exc}")
    return InteractionProfile(
        tone=Tone(values[InteractionSetting.TONE]),
        banter=BanterLevel(values[InteractionSetting.BANTER]),
        verbosity=Verbosity(values[InteractionSetting.VERBOSITY]),
        coaching_style=CoachingStyle(values[InteractionSetting.COACHING_STYLE]),
        proactivity=Proactivity(values[InteractionSetting.PROACTIVITY]),
        language=LanguagePreference(values[InteractionSetting.LANGUAGE]),
        warnings=tuple(warnings),
    )


class InteractionProfileService:
    """Read and safely update interaction settings through profile versioning."""

    def __init__(self, profile: FinancialProfileService) -> None:
        self.profile = profile

    async def active_profile(self) -> InteractionProfile:
        return compile_interaction_profile(await self.profile.active_entries())

    async def draft_update(
        self,
        *,
        setting: InteractionSetting,
        value: str,
        rationale: str,
    ) -> FinancialProfileUpdateDraft:
        normalized = normalize_interaction_value(setting, value)
        return await self.profile.draft_update(
            name=f"Assistant {setting.display_name}",
            key=setting.key,
            kind=ProfileKind.PREFERENCE,
            scopes=("Conversation",),
            statement=normalized,
            rationale=rationale,
        )

    def prepared_update(
        self,
        *,
        setting: InteractionSetting,
        value: str,
        rationale: str,
        current_page_id: str | None,
        current_statement: str | None,
        current_last_edited_at: datetime | None,
    ) -> FinancialProfileUpdateDraft:
        normalized = normalize_interaction_value(setting, value)
        normalized_rationale = rationale.strip()
        if not normalized_rationale:
            raise InvalidInteractionPreference("Interaction update rationale cannot be empty")
        return FinancialProfileUpdateDraft(
            name=f"Assistant {setting.display_name}",
            key=setting.key,
            kind=ProfileKind.PREFERENCE,
            scopes=("Conversation",),
            statement=normalized,
            rationale=normalized_rationale,
            current_page_id=current_page_id,
            current_statement=current_statement,
            current_last_edited_at=current_last_edited_at,
        )

    async def apply_update(
        self, draft: FinancialProfileUpdateDraft
    ) -> AppliedFinancialProfileUpdate:
        settings_by_key = {setting.key: setting for setting in InteractionSetting}
        setting = settings_by_key.get(draft.key)
        if setting is None:
            raise InvalidInteractionPreference("Draft key is not an interaction setting")
        if draft.kind is not ProfileKind.PREFERENCE or draft.scopes != ("Conversation",):
            raise InvalidInteractionPreference(
                "Interaction drafts must remain Conversation preferences"
            )
        normalize_interaction_value(setting, draft.statement)
        if not draft.rationale:
            raise InvalidInteractionPreference("Interaction update rationale cannot be empty")
        return await self.profile.apply_update(draft)
