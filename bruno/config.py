"""Environment-backed configuration for the Bruno transport shell."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from financial_agent.services.expense_classification import ClassificationMode
from pydantic import SecretStr


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
    typesafe_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    jev_model: str = "jev-latest"
    jev_transaction_threshold: float = 0.8
    expense_classifier_mode: ClassificationMode = ClassificationMode.APPLY
    expense_classification_threshold: float = 0.8


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
    jev_threshold = float(os.getenv("BRUNO_JEV_TRANSACTION_THRESHOLD", "0.8"))
    if not 0 < jev_threshold <= 1:
        raise ValueError(
            "BRUNO_JEV_TRANSACTION_THRESHOLD must be greater than 0 and at most 1"
        )
    typesafe_api_key = os.getenv("TYPESAFE_API_KEY", "").strip()
    tavily_api_key = os.getenv("TAVILY_API_KEY", "").strip()
    classifier_mode_value = os.getenv(
        "BRUNO_EXPENSE_CLASSIFIER_MODE", "apply"
    ).strip().casefold()
    try:
        classifier_mode = ClassificationMode(classifier_mode_value)
    except ValueError as exc:
        raise ValueError(
            "BRUNO_EXPENSE_CLASSIFIER_MODE must be off, shadow, or apply"
        ) from exc
    classification_threshold = float(
        os.getenv("BRUNO_EXPENSE_CLASSIFICATION_THRESHOLD", "0.8")
    )
    if not 0 < classification_threshold <= 1:
        raise ValueError(
            "BRUNO_EXPENSE_CLASSIFICATION_THRESHOLD must be greater than 0 and at most 1"
        )
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
        typesafe_api_key=(SecretStr(typesafe_api_key) if typesafe_api_key else None),
        tavily_api_key=(SecretStr(tavily_api_key) if tavily_api_key else None),
        jev_model=os.getenv("BRUNO_JEV_MODEL", "jev-latest").strip(),
        jev_transaction_threshold=jev_threshold,
        expense_classifier_mode=classifier_mode,
        expense_classification_threshold=classification_threshold,
    )
