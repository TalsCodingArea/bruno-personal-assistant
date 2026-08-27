"""Structured, user-adjustable conversation preferences."""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.profile import FinancialProfileEntry


class InteractionSetting(StrEnum):
    TONE = "tone"
    BANTER = "banter"
    VERBOSITY = "verbosity"
    COACHING_STYLE = "coaching_style"
    PROACTIVITY = "proactivity"
    LANGUAGE = "language"

    @property
    def key(self) -> str:
        return f"assistant.{self.value}"

    @property
    def display_name(self) -> str:
        return self.value.replace("_", " ").title()


class Tone(StrEnum):
    WARM = "warm"
    NEUTRAL = "neutral"
    PROFESSIONAL = "professional"
    CASUAL = "casual"


class BanterLevel(StrEnum):
    NONE = "none"
    LIGHT = "light"
    PLAYFUL = "playful"


class Verbosity(StrEnum):
    CONCISE = "concise"
    BALANCED = "balanced"
    DETAILED = "detailed"


class CoachingStyle(StrEnum):
    GENTLE = "gentle"
    DIRECT = "direct"
    CHALLENGING = "challenging"


class Proactivity(StrEnum):
    REACTIVE = "reactive"
    BALANCED = "balanced"
    PROACTIVE = "proactive"


class LanguagePreference(StrEnum):
    MATCH_USER = "match_user"
    ENGLISH = "english"
    HEBREW = "hebrew"


@dataclass(frozen=True, slots=True)
class InteractionProfile:
    """Effective conversational behavior after defaults and Notion overrides."""

    tone: Tone = Tone.WARM
    banter: BanterLevel = BanterLevel.LIGHT
    verbosity: Verbosity = Verbosity.CONCISE
    coaching_style: CoachingStyle = CoachingStyle.DIRECT
    proactivity: Proactivity = Proactivity.BALANCED
    language: LanguagePreference = LanguagePreference.MATCH_USER
    warnings: tuple[str, ...] = ()


INTERACTION_VALUE_TYPES = {
    InteractionSetting.TONE: Tone,
    InteractionSetting.BANTER: BanterLevel,
    InteractionSetting.VERBOSITY: Verbosity,
    InteractionSetting.COACHING_STYLE: CoachingStyle,
    InteractionSetting.PROACTIVITY: Proactivity,
    InteractionSetting.LANGUAGE: LanguagePreference,
}

INTERACTION_DEFAULTS = InteractionProfile()
INTERACTION_KEY_PREFIX = "assistant."
INTERACTION_KEYS = frozenset(setting.key for setting in InteractionSetting)


def is_interaction_key(key: str) -> bool:
    return key.startswith(INTERACTION_KEY_PREFIX)


def is_interaction_entry(entry: FinancialProfileEntry) -> bool:
    """Identify entries owned by the interaction-profile subsystem."""

    return is_interaction_key(entry.key)
