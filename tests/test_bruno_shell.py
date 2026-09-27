"""Focused tests for Bruno's retained transport behavior."""

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

from financial_agent.domain.models import Transaction
from financial_agent.services.expense_automation import ExpenseAutomationService
from financial_agent.tools.automation.expenses import build_expense_automation_tools
from langchain_core.messages import HumanMessage, SystemMessage
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
    process_created_expense,
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


class FakeCreatedExpenseRuntime:
    def __init__(self, *, classification_fails: bool = False) -> None:
        self.events: list[str] = []
        self.classification_fails = classification_fails

    async def classify_created_expense(self, page_id: str) -> dict[str, Any]:
        self.events.append(f"classify:{page_id}")
        if self.classification_fails:
            raise RuntimeError("classifier unavailable")
        return {"stage": "skipped", "would_apply": False, "applied": False}

    async def analyze_created_expense(self, page_id: str) -> dict[str, str]:
        self.events.append(f"analyze:{page_id}")
        return {"severity": "informational", "summary": "No material issue."}


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
        assert any(
            isinstance(message, SystemMessage)
            and "duplicate current operational versions" in str(message.content)
            for message in model.calls[-1]
        )

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
    assert normalize_automation_arguments(
        "handle_cal_notification",
        {"Text": "A charge at Coffee", "Tag": "Tal 👨🏻"},
    ) == {"text": "A charge at Coffee", "tags": ["Tal 👨🏻"]}


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


def test_bank_record_payload_dispatches_with_empty_arguments() -> None:
    async def scenario() -> None:
        message = FakeAutomationMessage('{"args":{},"tool":"new_bank_record"}')
        runtime = FakeAutomationRuntime()

        await handle_automation_text(
            message,
            object(),  # type: ignore[arg-type]
            runtime,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        )

        assert runtime.calls == [("new_bank_record", {})]
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


def test_created_expense_is_classified_before_financial_analysis(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = FakeCreatedExpenseRuntime()
        settings = _bruno_settings(tmp_path)

        await process_created_expense(
            runtime,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            settings,
            page_id="expense-1",
            fallback_chat=123,
        )

        assert runtime.events == ["classify:expense-1", "analyze:expense-1"]

    asyncio.run(scenario())


def test_classifier_failure_does_not_block_financial_analysis(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = FakeCreatedExpenseRuntime(classification_fails=True)

        await process_created_expense(
            runtime,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            _bruno_settings(tmp_path),
            page_id="expense-2",
            fallback_chat=123,
        )

        assert runtime.events == ["classify:expense-2", "analyze:expense-2"]

    asyncio.run(scenario())


def _bruno_settings(tmp_path: Path) -> Any:
    from bruno.config import BrunoSettings, TelegramChannels

    return BrunoSettings(
        bot_token="token",
        channels=TelegramChannels(
            receipts="",
            personal_assistant="123",
            logs="",
            automations="",
        ),
        receipt_category_options=(),
        receipt_model="receipt-model",
        router_model="router-model",
        expense_checkup_delay_seconds=0,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
        scheduler_path=tmp_path / "scheduler.sqlite3",
    )


def test_receipt_mapping_uses_finance_expense_fields() -> None:
    fields = receipt_expense_fields(
        {"vendor": "Market", "total_amount": 50, "category": "Groceries"}
    )

    assert fields["category"] == ["Home 🏡"]
    assert fields["subcategory"] == ["Groceries 🛒"]


def test_israeli_receipt_date_prefers_day_month_year_and_recent_interpretation() -> None:
    fields = receipt_expense_fields(
        {
            "vendor": "Israeli Market",
            "total_amount": 50,
            "country": "Israel",
            "date_text": "22/09/26",
            "date": "2022-09-26",
        },
        today=date(2026, 9, 27),
    )

    assert fields["occurred_on"] == "2026-09-22"


def test_receipt_mapping_drops_implausibly_old_date() -> None:
    fields = receipt_expense_fields(
        {
            "vendor": "Market",
            "total_amount": 50,
            "date": "2022-09-26",
        },
        today=date(2026, 9, 27),
    )

    assert fields["occurred_on"] is None


def test_israeli_receipt_recovers_reversed_short_date_without_raw_text() -> None:
    fields = receipt_expense_fields(
        {
            "vendor": "Israeli Market",
            "total_amount": 50,
            "currency": "ILS",
            "language": "Hebrew",
            "date": "2022-09-26",
        },
        today=date(2026, 9, 27),
    )

    assert fields["occurred_on"] == "2026-09-22"


def test_receipt_mapping_keeps_reasonable_recent_iso_date() -> None:
    fields = receipt_expense_fields(
        {
            "vendor": "Market",
            "total_amount": 50,
            "date": "2026-08-31",
        },
        today=date(2026, 9, 27),
    )

    assert fields["occurred_on"] == "2026-08-31"


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


def test_receipt_flow_corrects_israeli_short_date_before_notion_write(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        notion = FakeNotion()
        expenses = ExpenseAutomationService(
            notion,
            "expenses",
            reader=FakeFinanceReader(),
        )
        runtime = FakeReceiptRuntime(expenses)
        invoice = tmp_path / "israeli-receipt.pdf"
        invoice.write_bytes(b"%PDF-test")

        logged, created = await store_receipt_expense(
            runtime,  # type: ignore[arg-type]
            {
                "vendor": "Israeli Market",
                "total_amount": "50.00",
                "currency": "ILS",
                "language": "Hebrew",
                "country": "Israel",
                "date_text": "22/09/26",
                "date": "2022-09-26",
            },
            invoice_path=invoice,
            invoice_name="israeli-receipt.pdf",
            today=date(2026, 9, 27),
        )

        assert created is True
        assert logged.occurred_on == "2026-09-22"
        assert notion.created[0]["properties"]["Date"] == {
            "date": {"start": "2026-09-22"}
        }

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
