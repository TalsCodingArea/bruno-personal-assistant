"""Use cases for reading, drafting, and versioning durable financial context."""

import re
from datetime import datetime

from financial_agent.domain.operational_context import is_operational_context_key
from financial_agent.domain.profile import (
    AppliedFinancialProfileUpdate,
    FinancialProfileEntry,
    FinancialProfileUpdateDraft,
    ProfileKind,
)
from financial_agent.services.ports import FinancialProfileRepository

PROFILE_KEY = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


class ProfileVersionConflict(RuntimeError):
    """The active Notion version changed after the draft was prepared."""


class FinancialProfileService:
    """Keep durable context compact, explicit, and append-versioned."""

    def __init__(self, repository: FinancialProfileRepository) -> None:
        self.repository = repository

    async def active_entries(self, *, limit: int = 100) -> tuple[FinancialProfileEntry, ...]:
        entries = await self.repository.active_entries(limit=limit)
        seen: set[str] = set()
        duplicates: set[str] = set()
        for entry in entries:
            if entry.key in seen:
                duplicates.add(entry.key)
            seen.add(entry.key)
        if duplicates:
            keys = ", ".join(sorted(duplicates))
            raise ProfileVersionConflict(f"Multiple Active profile versions found for: {keys}")
        return entries

    async def draft_update(
        self,
        *,
        name: str,
        key: str,
        kind: ProfileKind,
        scopes: tuple[str, ...],
        statement: str,
        rationale: str,
    ) -> FinancialProfileUpdateDraft:
        name = name.strip()
        key = key.strip().casefold()
        statement = statement.strip()
        rationale = rationale.strip()
        normalized_scopes = tuple(dict.fromkeys(scope.strip() for scope in scopes if scope.strip()))
        if not name or not statement or not rationale:
            raise ValueError("Name, statement, and rationale cannot be empty")
        if not PROFILE_KEY.fullmatch(key):
            raise ValueError("Key must use lowercase words separated by '.', '_' or '-'")
        if is_operational_context_key(key):
            raise ValueError("monitoring.runtime.* keys are reserved for system context")
        if len(name) > 200 or len(statement) > 1800 or len(rationale) > 1800:
            raise ValueError("Profile draft text is too long for the compact Notion schema")
        current = await self.repository.active_entries(key=key, limit=2)
        if len(current) > 1:
            raise ProfileVersionConflict(f"Multiple Active profile versions found for: {key}")
        existing = current[0] if current else None
        return FinancialProfileUpdateDraft(
            name=name,
            key=key,
            kind=kind,
            scopes=normalized_scopes,
            statement=statement,
            rationale=rationale,
            current_page_id=existing.page_id if existing else None,
            current_statement=existing.statement if existing else None,
            current_last_edited_at=existing.last_edited_at if existing else None,
        )

    async def apply_update(
        self, draft: FinancialProfileUpdateDraft
    ) -> AppliedFinancialProfileUpdate:
        """Apply an already approved draft using optimistic version checks."""

        current = await self.repository.active_entries(key=draft.key, limit=3)
        exact = next((entry for entry in current if entry.statement == draft.statement), None)
        if exact is not None:
            return AppliedFinancialProfileUpdate(
                page_id=exact.page_id,
                operation_id=exact.operation_id,
                superseded_page_id=None,
                already_current=True,
            )
        if len(current) > 1:
            raise ProfileVersionConflict(
                f"Refusing to write while {len(current)} Active versions exist for {draft.key}"
            )
        existing = current[0] if current else None
        self._assert_draft_is_current(draft, existing)
        created = await self.repository.create_active_version(draft)
        if existing is not None:
            await self.repository.mark_superseded(existing.page_id)
        return AppliedFinancialProfileUpdate(
            page_id=created.page_id,
            operation_id=created.operation_id,
            superseded_page_id=existing.page_id if existing else None,
        )

    @staticmethod
    def _assert_draft_is_current(
        draft: FinancialProfileUpdateDraft, existing: FinancialProfileEntry | None
    ) -> None:
        existing_id = existing.page_id if existing else None
        if existing_id != draft.current_page_id:
            raise ProfileVersionConflict("The Active profile page changed after this draft")
        expected_time: datetime | None = draft.current_last_edited_at
        actual_time = existing.last_edited_at if existing else None
        if expected_time != actual_time:
            raise ProfileVersionConflict("The Active profile page was edited after this draft")
