"""Bank-import completion and financial-evaluation notifications."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bruno.bank_notifications import format_bank_import_evaluation
from bruno.config import BrunoSettings, TelegramChannels
from bruno.telegram_bot.automations import handle_automation_text

EVALUATION = {
    "account_outlook": {
        "latest_movement": {"balance_after": "12000.00"},
        "expense_settlement": {
            "credit_charges": "3000.00",
            "expected_reimbursement": "500.00",
        },
        "projected_balance_after_settlement": "9000.00",
        "minimum_available_balance": "7500.00",
        "available_above_minimum": "1500.00",
        "on_track": True,
        "recommended_savings_transfer": "0.00",
    },
    "budget": {
        "total_budget": "5000.00",
        "actual_variable_spend": "300.00",
        "actual_overspends": [],
        "material_projections": [],
    },
}


class Message:
    text = '{"tool":"new_bank_record","args":{}}'
    caption = None
    chat_id = 456

    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class Bot:
    def __init__(self) -> None:
        self.sent: list[tuple[str | int, str]] = []

    async def send_message(self, *, chat_id: str | int, text: str) -> None:
        self.sent.append((chat_id, text))


class Runtime:
    async def invoke_automation(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        assert tool_name == "new_bank_record"
        return {
            "status": "complete",
            "files": 1,
            "transactions": 2,
            "created": 2,
            "skipped": 0,
            "conflicts": 0,
            "message": "✅ Bank import complete",
        }

    async def current_financial_evaluation(self) -> dict[str, Any]:
        return EVALUATION


def settings(tmp_path: Path) -> BrunoSettings:
    return BrunoSettings(
        bot_token="token",
        channels=TelegramChannels("", "123", "", "456"),
        receipt_category_options=(),
        receipt_model="receipt",
        router_model="router",
        expense_checkup_delay_seconds=600,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
        scheduler_path=tmp_path / "scheduler.sqlite3",
    )


def test_completion_report_includes_cash_and_budget_evaluation() -> None:
    message = format_bank_import_evaluation(
        {
            "status": "complete",
            "files": 1,
            "transactions": 2,
            "created": 2,
            "skipped": 0,
            "conflicts": 0,
        },
        EVALUATION,
    )

    assert "Bank Excel analysis finished" in message
    assert "Bank balance: ₪12000.00" in message
    assert "on track" in message
    assert "no material category exception" in message


def test_completed_automation_sends_evaluation_to_personal_chat(tmp_path: Path) -> None:
    async def scenario() -> None:
        message = Message()
        bot = Bot()
        context = SimpleNamespace(bot=bot)

        await handle_automation_text(
            message,
            context,  # type: ignore[arg-type]
            Runtime(),  # type: ignore[arg-type]
            settings(tmp_path),
        )

        assert message.replies == ["✅ Bank import complete"]
        assert bot.sent[0][0] == "123"
        assert "Bank Excel analysis finished" in bot.sent[0][1]
        assert "After next settlement" in bot.sent[0][1]

    asyncio.run(scenario())
