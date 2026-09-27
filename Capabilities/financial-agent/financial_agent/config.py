"""Typed application configuration loaded from environment variables."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from financial_agent.domain.currency import DEFAULT_CURRENCY, Currency
from financial_agent.domain.expense_monitoring import ExpenseMonitorMode


class Environment(StrEnum):
    """Supported deployment environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class ReasoningEffort(StrEnum):
    """Supported GPT-5.6 reasoning-effort settings."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class Settings(BaseSettings):
    """Runtime settings.

    Values are read from ``.env`` and variables prefixed with ``FINANCE_AGENT_``.
    Secrets use ``SecretStr`` so they are redacted in logs and representations.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FINANCE_AGENT_",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    environment: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"
    currency: Currency = DEFAULT_CURRENCY

    notion_token: SecretStr | None = None
    notion_api_version: str = "2026-03-11"
    expenses_data_source_id: str | None = None
    income_data_source_id: str | None = None
    budgets_data_source_id: str | None = None
    future_expenses_data_source_id: str | None = None
    financial_rules_data_source_id: str | None = None
    bank_account_notion_database_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "BANK_ACCOUNT_NOTION_DATABASE_ID",
            "FINANCE_AGENT_BANK_ACCOUNT_NOTION_DATABASE_ID",
        ),
    )
    bank_account_notion_data_source_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "BANK_ACCOUNT_NOTION_DATA_SOURCE_ID",
            "FINANCE_AGENT_BANK_ACCOUNT_NOTION_DATA_SOURCE_ID",
        ),
    )
    model_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENAI_API_KEY", "FINANCE_AGENT_MODEL_API_KEY"
        ),
    )
    model_name: str = "gpt-5.6-terra"
    model_reasoning_effort: ReasoningEffort = ReasoningEffort.MEDIUM
    expense_monitor_mode: ExpenseMonitorMode = ExpenseMonitorMode.SHADOW
    expense_monitor_ledger_path: Path = Path(
        ".langgraph_api/expense-monitor.sqlite3"
    )

    langsmith_tracing: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "LANGSMITH_TRACING", "FINANCE_AGENT_LANGSMITH_TRACING"
        ),
    )
    langsmith_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LANGSMITH_API_KEY", "FINANCE_AGENT_LANGSMITH_API_KEY"
        ),
    )
    langsmith_project: str = Field(
        default="finance-agent",
        validation_alias=AliasChoices(
            "LANGSMITH_PROJECT", "FINANCE_AGENT_LANGSMITH_PROJECT"
        ),
    )

    def require_notion_data_source_ids(self) -> dict[str, str]:
        """Return configured Notion sources or fail with every missing variable."""

        values = {
            "expenses": self.expenses_data_source_id,
            "income": self.income_data_source_id,
            "budgets": self.budgets_data_source_id,
            "future_expenses": self.future_expenses_data_source_id,
            "financial_rules": self.financial_rules_data_source_id,
        }
        missing = [
            f"FINANCE_AGENT_{name.upper()}_DATA_SOURCE_ID"
            for name, value in values.items()
            if value is None or not value.strip()
        ]
        if missing:
            raise ValueError(f"Missing Notion data-source settings: {', '.join(missing)}")
        return {name: value.strip() for name, value in values.items() if value is not None}


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings instance per application process."""

    return Settings()
