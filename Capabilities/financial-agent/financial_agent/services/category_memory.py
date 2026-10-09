"""Explicit merchant corrections stored in the existing versioned Financial Rules."""

import hashlib
import json
import unicodedata

from financial_agent.domain.profile import FinancialProfileUpdateDraft, ProfileKind
from financial_agent.services.profile import FinancialProfileService, ProfileVersionConflict


def merchant_identity(merchant: str) -> str:
    """Match spelling/case/spacing only; do not conflate branches or purchase details."""
    return " ".join(unicodedata.normalize("NFKC", merchant).casefold().split())


def _key(merchant: str) -> str:
    identity = merchant_identity(merchant)
    if not identity:
        raise ValueError("Cannot remember an empty merchant")
    return "expense.category." + hashlib.sha256(identity.encode()).hexdigest()


class CategoryMemory:
    def __init__(self, profile: FinancialProfileService) -> None:
        self.profile = profile

    async def lookup(self, merchant: str) -> tuple[str, str] | None:
        if not merchant_identity(merchant):
            return None
        entries = await self.profile.repository.active_entries(key=_key(merchant), limit=2)
        if len(entries) > 1:
            raise ProfileVersionConflict("Multiple active merchant category rules")
        if not entries:
            return None
        value = json.loads(entries[0].statement)
        if not isinstance(value, dict) or value.get("merchant") != merchant_identity(merchant):
            raise ValueError("Invalid merchant category rule")
        category, subcategory = value.get("category"), value.get("subcategory")
        if not all(isinstance(item, str) and item.strip() for item in (category, subcategory)):
            raise ValueError("Invalid merchant category pair")
        return str(category), str(subcategory)

    async def draft(
        self, merchant: str, category: str, subcategory: str, rationale: str
    ) -> FinancialProfileUpdateDraft:
        return await self.profile.draft_update(
            name=f"Category for {merchant}"[:200],
            key=_key(merchant),
            kind=ProfileKind.DECISION,
            scopes=("Spending",),
            statement=json.dumps(
                {
                    "merchant": merchant_identity(merchant),
                    "category": category,
                    "subcategory": subcategory,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            rationale=rationale,
        )

    async def save(self, draft: FinancialProfileUpdateDraft) -> None:
        await self.profile.apply_update(draft)
