"""Environment-backed configuration for the Bruno transport shell."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class TelegramChannels:
    receipts: str
    personal_assistant: str
    logs: str
    automations: str


@dataclass(frozen=True, slots=True)
class BrunoSettings:
    bot_token: str
    channels: TelegramChannels
    receipt_category_options: tuple[str, ...]
    receipt_model: str
    router_model: str
    expense_checkup_delay_seconds: float
    checkpoint_path: Path
    scheduler_path: Path


def load_bruno_settings() -> BrunoSettings:
    load_dotenv()
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValueError("Missing TELEGRAM_BOT_TOKEN environment variable")
    categories = tuple(
        item.strip()
        for item in os.getenv(
            "RECEIPT_CATEGORY_OPTIONS",
            "Groceries,Restaurant,Bills,EV,Online Services,Therapy,Decor",
        ).split(",")
        if item.strip()
    )
    delay = float(os.getenv("BRUNO_EXPENSE_CHECKUP_DELAY_SECONDS", "600"))
    if delay < 0:
        raise ValueError("BRUNO_EXPENSE_CHECKUP_DELAY_SECONDS cannot be negative")
    return BrunoSettings(
        bot_token=bot_token,
        channels=TelegramChannels(
            receipts=os.getenv("TELEGRAM_CHAT_ID_RECEIPTS", "").strip(),
            personal_assistant=os.getenv(
                "TELEGRAM_CHAT_ID_PERSONAL_ASSISTANT", ""
            ).strip(),
            logs=os.getenv("TELEGRAM_CHAT_ID_LOGS", "").strip(),
            automations=os.getenv("TELEGRAM_CHAT_ID_AUTOMATIONS", "").strip(),
        ),
        receipt_category_options=categories,
        receipt_model=os.getenv("BRUNO_RECEIPT_MODEL", "gpt-4o").strip(),
        router_model=os.getenv("BRUNO_ROUTER_MODEL", "gpt-5.6-luna").strip(),
        expense_checkup_delay_seconds=delay,
        checkpoint_path=Path(
            os.getenv("BRUNO_CHECKPOINT_PATH", ".bruno/checkpoints.sqlite3")
        ),
        scheduler_path=Path(
            os.getenv("BRUNO_SCHEDULER_PATH", ".bruno/scheduler.sqlite3")
        ),
    )
