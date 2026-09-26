"""Plain objects for durable financial preferences and decisions."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ProfileKind(StrEnum):
    PREFERENCE = "Preference"
    DECISION = "Decision"
    CONSTRAINT = "Constraint"
    GOAL = "Goal"


class ProfileStatus(StrEnum):
    ACTIVE = "Active"
    SUPERSEDED = "Superseded"
    ARCHIVED = "Archived"


@dataclass(frozen=True, slots=True)
class FinancialProfileEntry:
    """One versioned Financial Rules page mapped out of Notion."""

    page_id: str
    name: str
    key: str
    kind: ProfileKind
    scopes: tuple[str, ...]
    statement: str
    status: ProfileStatus
    supersedes: tuple[str, ...] = ()
    operation_id: str | None = None
    created_at: datetime | None = None
    last_edited_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FinancialProfileUpdateDraft:
    """A side-effect-free proposal for one new profile-entry version."""

    name: str
    key: str
    kind: ProfileKind
    scopes: tuple[str, ...]
    statement: str
    rationale: str
    current_page_id: str | None = None
    current_statement: str | None = None
    current_last_edited_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AppliedFinancialProfileUpdate:
    """Result of an approved versioned profile mutation."""

    page_id: str
    operation_id: str | None
    superseded_page_id: str | None
    already_current: bool = False
