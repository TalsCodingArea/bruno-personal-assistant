"""Focused tests for Bruno's retained transport behavior."""

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

from financial_agent.domain.models import Transaction
from financial_agent.services.expense_automation import ExpenseAutomationService
from financial_agent.tools.automation.expenses import build_expense_automation_tools
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from bruno.checkups import format_checkup_message
from bruno.coordinator import CapabilityDecision, build_coordinator_graph
from bruno.debounce import CheckupDebouncer
from bruno.receipts import receipt_expense_fields
from bruno.telegram_bot.automations import (
    handle_automation_text,
    normalize_automation_arguments,
    normalize_expense_arguments,
    parse_automation_payload,
)
from bruno.telegram_bot.receipts import store_receipt_expense
from tests.fakes import FakeFinanceReader, FakeNotion


class FakeRouter:
    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    def with_structured_output(self, *_: Any, **__: Any) -> "FakeRouter":
        return self

    async def ainvoke(self, messages: list[Any], config: Any) -> CapabilityDecision:
        self.calls.append(messages)
        return CapabilityDecision(capability="finance", confidence=0.9)


class FakeReceiptRuntime:
    def __init__(self, expenses: ExpenseAutomationService) -> None:
        self.expenses = expenses
        self.tools = {
            tool.name: tool for tool in build_expense_automation_tools(expenses)
        }

    async def invoke_automation(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.tools[tool_name].ainvoke(arguments)


class FakeAutomationMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.caption = None
        self.chat_id = 123
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeAutomationRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke_automation(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        from bruno.runtime import AutomationToolNotFoundError

        if tool_name == "missing":
            raise AutomationToolNotFoundError(tool_name)
        self.calls.append((tool_name, arguments))
        return {"message": "automation ran"}


def test_one_node_router_retains_thread_context() -> None:
    async def scenario() -> None:
        model = FakeRouter()
        graph = build_coordinator_graph(model, InMemorySaver())
        config = {"configurable": {"thread_id": "telegram:1:coordinator"}}

        await graph.ainvoke({"messages": [HumanMessage(content="Check my budget")]}, config)
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="What about 500?")]}, config
        )

        assert state["active_capability"] == "finance"
        assert any(message.content == "Check my budget" for message in model.calls[-1])

    asyncio.run(scenario())


def test_debounce_runs_only_latest_callback_after_quiet_period() -> None:
    async def scenario() -> None:
        gates: list[asyncio.Event] = []
        calls: list[str] = []

        async def controlled_sleep(_: float) -> None:
            gate = asyncio.Event()
            gates.append(gate)
            await gate.wait()

        debouncer = CheckupDebouncer(600, sleep=controlled_sleep)
        debouncer.schedule("expenses", lambda: _record(calls, "first"))
        debouncer.schedule("expenses", lambda: _record(calls, "second"))
        await asyncio.sleep(0)
        assert len(gates) == 2
        for gate in gates:
            gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert calls == ["second"]
        await debouncer.aclose()

    asyncio.run(scenario())


async def _record(calls: list[str], value: str) -> None:
    calls.append(value)


def test_existing_automation_payload_is_normalized() -> None:
    tool, arguments = parse_automation_payload(
        '{"tool":"log_expense","args":{"Description":"Coffee","Amount":12.5}}'
    )

    assert tool == "log_expense"
    assert normalize_expense_arguments(arguments) == {
        "description": "Coffee",
        "amount": "12.5",
    }

    assert normalize_automation_arguments(
        "auto_expense_tool",
        {"Description": "Coffee", "Amount": 12.5},
    ) == {"description": "Coffee", "amount": 12.5}


def test_automation_handler_dispatches_named_registered_tool() -> None:
    async def scenario() -> None:
        message = FakeAutomationMessage(
            '{"tool":"future_automation","args":{"value":3}}'
        )
        runtime = FakeAutomationRuntime()

        await handle_automation_text(
            message,
            object(),  # type: ignore[arg-type]
            runtime,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        )

        assert runtime.calls == [("future_automation", {"value": 3})]
        assert message.replies == ["automation ran"]

    asyncio.run(scenario())


def test_automation_handler_reports_missing_tool() -> None:
    async def scenario() -> None:
        message = FakeAutomationMessage('{"tool":"missing","args":{}}')

        await handle_automation_text(
            message,
            object(),  # type: ignore[arg-type]
            FakeAutomationRuntime(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        )

        assert message.replies == ["No automation tool found"]

    asyncio.run(scenario())


def test_receipt_mapping_uses_finance_expense_fields() -> None:
    fields = receipt_expense_fields(
        {"vendor": "Market", "total_amount": 50, "category": "Groceries"}
    )

    assert fields["category"] == ["Home 🏡"]
    assert fields["subcategory"] == ["Groceries 🛒"]


def test_receipt_flow_matches_same_date_and_amount_despite_different_names(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        notion = FakeNotion()
        reader = FakeFinanceReader(
            transactions=(
                Transaction(
                    "matched-expense",
                    "CARD TRANSACTION 8472",
                    date(2026, 9, 1),
                    "42.50",
                ),
                Transaction(
                    "different-amount",
                    "Market automated charge",
                    date(2026, 9, 1),
                    "19.00",
                ),
                Transaction(
                    "different-date",
                    "The Market Ltd.",
                    date(2026, 8, 31),
                    "42.50",
                ),
            )
        )
        expenses = ExpenseAutomationService(notion, "expenses", reader=reader)
        runtime = FakeReceiptRuntime(expenses)
        invoice = tmp_path / "market.pdf"
        invoice.write_bytes(b"%PDF-test")

        logged, created = await store_receipt_expense(
            runtime,  # type: ignore[arg-type]
            {
                "vendor": "The Market Ltd.",
                "total_amount": "42.50",
                "date": "2026-09-01",
                "category": "Groceries",
            },
            invoice_path=invoice,
            invoice_name="market.pdf",
        )

        assert created is False
        assert logged.page_id == "matched-expense"
        assert notion.created == []
        assert notion.updated[0]["page_id"] == "matched-expense"

    asyncio.run(scenario())


def test_checkup_message_only_requests_savings_for_unresolved_deficit() -> None:
    message = format_checkup_message(
        {
            "should_notify": True,
            "projections": [],
            "actual_overspends": [],
            "mutation": {"disposition": "applied", "reason": "done"},
            "budget_changes": [],
            "savings_required": True,
            "unresolved_variable_deficit": "125.00",
            "savings_amount": "175.00",
            "observations": ["variable_reserve_deficit"],
        }
    )

    assert message is not None
    assert "pull this amount from savings" in message
    assert "₪175.00" in message
