"""Trusted expense writes and tool registration."""

import asyncio
from datetime import date
from pathlib import Path

from financial_agent.domain.models import Transaction
from financial_agent.services.expense_automation import ExpenseAutomationService
from financial_agent.tools.automation.expenses import build_expense_automation_tools

from tests.fakes import FakeFinanceReader, FakeNotion


class FakeTextExpenseModel:
    def with_structured_output(self, schema: type[object]) -> "FakeTextExpenseModel":
        self.schema = schema
        return self

    async def ainvoke(self, messages: list[object]) -> object:
        return self.schema(description="Coffee Shop", amount=18.5)


def test_log_expense_builds_finance_schema_and_uploads_invoice(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        notion = FakeNotion()
        invoice = tmp_path / "receipt.pdf"
        invoice.write_bytes(b"%PDF-test")
        service = ExpenseAutomationService(notion, "expenses")

        result = await service.log_expense(
            description="Market",
            amount="42.50",
            occurred_on="2026-08-31",
            category=("Home 🏡",),
            subcategory=("Groceries 🛒",),
            invoice_path=invoice,
        )

        assert result.amount == "42.50"
        assert notion.uploaded[0]["content_type"] == "application/pdf"
        properties = notion.created[0]["properties"]
        assert properties["Amount"] == {"number": 42.5}
        assert properties["Category"]["multi_select"] == [{"name": "Home 🏡"}]
        assert properties["Invoice"]["files"][0]["file_upload"]["id"] == "upload-1"

    asyncio.run(scenario())


def test_log_expense_tool_is_separate_from_conversation_catalog() -> None:
    async def scenario() -> None:
        notion = FakeNotion()
        service = ExpenseAutomationService(notion, "expenses")
        tool = next(
            item
            for item in build_expense_automation_tools(service)
            if item.name == "log_expense"
        )

        result = await tool.ainvoke(
            {
                "description": "Coffee",
                "amount": "12.00",
                "occurred_on": "2026-08-31",
            }
        )

        assert tool.name == "log_expense"
        assert "timezone" in tool.args_schema.model_json_schema()["properties"]
        assert result["description"] == "Coffee"
        assert notion.created[0]["properties"]["Category"]["multi_select"] == [
            {"name": "Uncategorized"}
        ]

    asyncio.run(scenario())


def test_log_expense_converts_utc_timestamp_to_gmt_plus_three_date() -> None:
    async def scenario() -> None:
        notion = FakeNotion()
        service = ExpenseAutomationService(notion, "expenses")
        tool = next(
            item
            for item in build_expense_automation_tools(service)
            if item.name == "log_expense"
        )

        result = await tool.ainvoke(
            {
                "description": "Late purchase",
                "amount": "12.00",
                "occurred_on": "2026-08-31T22:30:00Z",
                "timezone": "GMT+03:00",
            }
        )

        assert result["occurred_on"] == "2026-09-01"
        assert notion.created[0]["properties"]["Date"] == {
            "date": {"start": "2026-09-01"}
        }

    asyncio.run(scenario())


def test_auto_expense_and_text_expense_are_registered() -> None:
    async def scenario() -> None:
        notion = FakeNotion()
        service = ExpenseAutomationService(notion, "expenses")
        tools = {
            item.name: item
            for item in build_expense_automation_tools(
                service,
                text_model=FakeTextExpenseModel(),  # type: ignore[arg-type]
            )
        }

        automatic = await tools["auto_expense_tool"].ainvoke(
            {"description": "Market", "amount": "₪75.00"}
        )
        extracted = await tools["log_txt_expense"].ainvoke(
            {"text": "Card purchase for 18.5 ILS at Coffee Shop"}
        )

        assert {"log_expense", "auto_expense_tool", "log_txt_expense"} <= tools.keys()
        assert automatic["amount"] == "75.00"
        assert extracted["description"] == "Coffee Shop"
        assert len(notion.created) == 2

    asyncio.run(scenario())


def test_auto_expense_skips_explicit_foreign_currency() -> None:
    async def scenario() -> None:
        notion = FakeNotion()
        service = ExpenseAutomationService(notion, "expenses")
        tool = next(
            item
            for item in build_expense_automation_tools(service)
            if item.name == "auto_expense_tool"
        )

        result = await tool.ainvoke(
            {"description": "Overseas purchase", "amount": "$15.00"}
        )

        assert result["status"] == "skipped"
        assert notion.created == []

    asyncio.run(scenario())


def test_same_day_lookup_and_attachment_update_without_creating_duplicate(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        notion = FakeNotion()
        reader = FakeFinanceReader(
            transactions=(
                Transaction(
                    "existing-expense",
                    "Market charge",
                    date(2026, 9, 1),
                    "42.50",
                    category="Home 🏡",
                    subcategory="Groceries 🛒",
                    payment_type="Credit",
                ),
            )
        )
        service = ExpenseAutomationService(notion, "expenses", reader=reader)
        lookup = next(
            item
            for item in build_expense_automation_tools(service)
            if item.name == "get_expenses_for_date"
        )

        result = await lookup.ainvoke({"occurred_on": "2026-09-01"})
        invoice = tmp_path / "receipt.pdf"
        invoice.write_bytes(b"%PDF-test")
        attached = await service.attach_receipt(
            page_id=result["expenses"][0]["page_id"],
            description=result["expenses"][0]["description"],
            amount=result["expenses"][0]["amount"],
            occurred_on=result["occurred_on"],
            invoice_path=invoice,
        )

        assert attached.page_id == "existing-expense"
        assert notion.created == []
        assert notion.updated[0]["page_id"] == "existing-expense"
        assert notion.updated[0]["properties"]["Invoice"]["files"][0]["file_upload"]

    asyncio.run(scenario())
