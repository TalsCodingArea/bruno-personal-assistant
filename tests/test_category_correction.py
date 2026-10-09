"""Conversation correction, durable learning, and failure boundaries."""

import asyncio
from dataclasses import replace
from datetime import date

import pytest
from financial_agent.domain.profile import FinancialProfileEntry, ProfileStatus
from financial_agent.services.category_correction import CategoryCorrectionService
from financial_agent.services.category_memory import CategoryMemory
from financial_agent.services.expense_classification import (
    ClassificationMode,
    ClassificationStage,
    ExpenseClassificationService,
)
from financial_agent.services.profile import FinancialProfileService
from financial_agent.tools.category_correction import build_category_correction_tools

from tests.fakes import FakeFinanceReader
from tests.test_expense_classification import policy, transaction


class ProfileRepository:
    def __init__(self):
        self.entries = []
        self.fail = False

    async def active_entries(self, *, key=None, limit=100):
        return tuple(
            e
            for e in self.entries
            if e.status == ProfileStatus.ACTIVE and (key is None or e.key == key)
        )[:limit]

    async def create_active_version(self, draft):
        if self.fail:
            raise RuntimeError("offline")
        entry = FinancialProfileEntry(
            page_id=str(len(self.entries)),
            name=draft.name,
            key=draft.key,
            kind=draft.kind,
            scopes=draft.scopes,
            statement=draft.statement,
            status=ProfileStatus.ACTIVE,
        )
        self.entries.append(entry)
        return entry

    async def mark_superseded(self, page_id):
        self.entries = [
            replace(e, status=ProfileStatus.SUPERSEDED) if e.page_id == page_id else e
            for e in self.entries
        ]


class Writer:
    def __init__(self, reader):
        self.reader = reader
        self.calls = []
        self.fail = False
        self.persist = True

    async def apply_classification(self, page_id, category, subcategory):
        if self.fail:
            raise RuntimeError("write failed")
        self.calls.append((page_id, category, subcategory))
        if self.persist:
            self.reader.transaction_rows = tuple(
                replace(
                    row,
                    category=category,
                    subcategory=subcategory,
                    category_options=(category,),
                    subcategory_options=(subcategory,),
                )
                if row.id == page_id
                else row
                for row in self.reader.transaction_rows
            )


def setup():
    reader = FakeFinanceReader(
        transactions=(
            transaction(
                "wrong", "Cafe 42", date(2026, 10, 1), category="Home", subcategory="Groceries"
            ),
            transaction(
                "allowed", "Other Cafe", date(2026, 9, 1), category="Food", subcategory="Dining"
            ),
            *(
                transaction(
                    str(i), "Cafe 42", date(2026, 9, 2), category="Home", subcategory="Groceries"
                )
                for i in range(10)
            ),
        )
    )
    repository = ProfileRepository()
    memory = CategoryMemory(FinancialProfileService(repository))
    writer = Writer(reader)
    service = CategoryCorrectionService(reader, writer, memory)
    tool = build_category_correction_tools(service)[1]
    args = dict(
        page_id="wrong",
        category="Food",
        subcategory="Dining",
        expected_category=["Home"],
        expected_subcategory=["Groceries"],
        remember=True,
        rationale="This merchant is a cafe; remember it.",
    )
    return reader, repository, memory, writer, tool, args


@pytest.mark.parametrize("mode", [ClassificationMode.APPLY, ClassificationMode.SHADOW])
def test_correction_tool_persists_rule_overrides_old_majority_after_restart(mode):
    async def scenario():
        reader, repository, _, writer, tool, args = setup()
        result = await tool.ainvoke(args)
        assert result["applied"] and result["remembered"]
        assert (await reader.transaction("wrong")).subcategory == "Dining"
        # A new receipt may already have a model-proposed category: the explicit rule wins.
        reader.transaction_rows += (
            transaction(
                "next", " CAFE 42 ", date(2026, 10, 9), category="Home", subcategory="Groceries"
            ),
        )
        classifier = ExpenseClassificationService(
            reader,
            writer,
            decider=None,
            search=None,
            policy=policy(mode),
            memory=CategoryMemory(FinancialProfileService(repository)),
        )
        outcome = await classifier.classify_created_expense("next", as_of=date(2026, 10, 9))
        assert outcome.stage is ClassificationStage.CORRECTION
        assert outcome.subcategory == "Dining"
        assert outcome.applied == (mode is ClassificationMode.APPLY)
        assert await classifier.memory.lookup("Cafe 43") is None

    asyncio.run(scenario())


def test_one_off_correction_does_not_create_rule():
    async def scenario():
        _, repository, _, _, tool, args = setup()
        result = await tool.ainvoke({**args, "remember": False})
        assert result["applied"] and not result["remembered"]
        assert repository.entries == []

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["write", "verify", "stale", "unknown_pair"])
def test_failed_or_stale_correction_does_not_teach(failure):
    async def scenario():
        _, repository, _, writer, tool, args = setup()
        if failure == "write":
            writer.fail = True
        elif failure == "verify":
            writer.persist = False
        elif failure == "stale":
            args["expected_category"] = ["Changed"]
        else:
            args["subcategory"] = "Invented"
        with pytest.raises((ValueError, RuntimeError)):
            await tool.ainvoke(args)
        assert repository.entries == []
        if failure in {"stale", "unknown_pair"}:
            assert writer.calls == []

    asyncio.run(scenario())


def test_partial_memory_failure_reports_written_expense_and_can_retry():
    async def scenario():
        _, repository, _, writer, tool, args = setup()
        repository.fail = True
        result = await tool.ainvoke(args)
        assert result["applied"] and not result["remembered"] and result["memory_error"]
        repository.fail = False
        result = await tool.ainvoke(args)
        assert result["remembered"]
        assert len(writer.calls) == 1

    asyncio.run(scenario())


def test_later_correction_replaces_rule():
    async def scenario():
        _, repository, memory, _, tool, args = setup()
        await tool.ainvoke(args)
        await tool.ainvoke(
            {
                **args,
                "category": "Home",
                "subcategory": "Groceries",
                "expected_category": ["Food"],
                "expected_subcategory": ["Dining"],
            }
        )
        assert await memory.lookup("Cafe 42") == ("Home", "Groceries")
        assert len(await repository.active_entries()) == 1

    asyncio.run(scenario())


def test_memory_lookup_failure_does_not_apply_old_history():
    async def scenario():
        reader, repository, memory, writer, tool, args = setup()
        await tool.ainvoke(args)
        repository.entries.append(repository.entries[0])  # conflicting active versions
        reader.transaction_rows += (transaction("next", "Cafe 42", date(2026, 10, 9)),)
        classifier = ExpenseClassificationService(
            reader, writer, decider=None, search=None, policy=policy(), memory=memory
        )
        result = await classifier.classify_created_expense("next", as_of=date(2026, 10, 9))
        assert result.stage is ClassificationStage.UNRESOLVED
        assert not result.applied
        assert len(writer.calls) == 1

    asyncio.run(scenario())


def test_memory_draft_failure_still_corrects_expense():
    async def scenario():
        _, repository, _, writer, tool, args = setup()
        await tool.ainvoke(args)
        repository.entries.append(repository.entries[0])
        result = await tool.ainvoke(
            {
                **args,
                "category": "Home",
                "subcategory": "Groceries",
                "expected_category": ["Food"],
                "expected_subcategory": ["Dining"],
            }
        )
        assert result["applied"] and not result["remembered"] and result["memory_error"]
        assert len(writer.calls) == 2

    asyncio.run(scenario())
