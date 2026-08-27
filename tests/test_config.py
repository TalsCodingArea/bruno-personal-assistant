"""Tests for application configuration."""

from app.config import Environment, Settings
from app.domain.expense_monitoring import ExpenseMonitorMode


def test_settings_have_safe_development_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.log_level == "INFO"
    assert settings.notion_token is None
    assert settings.notion_api_version == "2026-03-11"
    assert settings.langsmith_tracing is False
    assert settings.expense_monitor_mode is ExpenseMonitorMode.SHADOW
    assert settings.expense_monitor_ledger_path.name == "expense-monitor.sqlite3"


def test_settings_load_prefixed_environment_variables(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("FINANCE_AGENT_ENVIRONMENT", "test")
    monkeypatch.setenv("FINANCE_AGENT_NOTION_TOKEN", "notion-secret")

    settings = Settings(_env_file=None)

    assert settings.environment is Environment.TEST
    assert settings.notion_token is not None
    assert settings.notion_token.get_secret_value() == "notion-secret"
    assert "notion-secret" not in repr(settings)


def test_notion_source_ids_are_required_together_at_composition_time() -> None:
    settings = Settings(_env_file=None)

    try:
        settings.require_notion_data_source_ids()
    except ValueError as exc:
        assert "FINANCE_AGENT_FINANCIAL_RULES_DATA_SOURCE_ID" in str(exc)
        assert "FINANCE_AGENT_EXPENSES_DATA_SOURCE_ID" in str(exc)
    else:
        raise AssertionError("Expected missing source IDs to be reported")
