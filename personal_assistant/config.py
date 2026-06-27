from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class TelegramChannels:
    receipts: str
    personal_assistant: str
    logs: str
    automations: str

    def as_dict(self) -> Dict[str, str]:
        return {
            "receipts": self.receipts,
            "personal_assistant": self.personal_assistant,
            "logs": self.logs,
            "automations": self.automations,
        }


@dataclass(frozen=True)
class Settings:
    bot_token: str
    channels: TelegramChannels
    receipt_category_options: list[str]


def load_settings() -> Settings:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise ValueError("Missing TELEGRAM_BOT_TOKEN environment variable.")

    receipt_category_options = [
        item.strip()
        for item in os.getenv(
            "RECEIPT_CATEGORY_OPTIONS",
            "Groceries,Decor,Restaurant,Bills,EV,Online Services,Therapy",
        ).split(",")
        if item.strip()
    ]

    return Settings(
        bot_token=bot_token,
        channels=TelegramChannels(
            receipts=os.getenv("TELEGRAM_CHAT_ID_RECEIPTS", ""),
            personal_assistant=os.getenv("TELEGRAM_CHAT_ID_PERSONAL_ASSISTANT", ""),
            logs=os.getenv("TELEGRAM_CHAT_ID_LOGS", ""),
            automations=os.getenv("TELEGRAM_CHAT_ID_AUTOMATIONS", ""),
        ),
        receipt_category_options=receipt_category_options,
    )


settings = load_settings()
