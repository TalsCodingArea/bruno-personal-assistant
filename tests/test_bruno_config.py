"""Configuration policy for Bruno's automatic expense classifier."""

import pytest
from financial_agent.services.expense_classification import ClassificationMode

from bruno.config import load_bruno_settings


@pytest.fixture(autouse=True)
def prevent_local_dotenv_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bruno.config.load_dotenv", lambda: False)


def test_expense_classifier_configuration_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-secret")
    monkeypatch.setenv("BRUNO_EXPENSE_CLASSIFIER_MODE", "apply")
    monkeypatch.setenv("BRUNO_EXPENSE_CLASSIFICATION_THRESHOLD", "0.76")

    settings = load_bruno_settings()

    assert settings.expense_classifier_mode is ClassificationMode.APPLY
    assert settings.expense_classification_threshold == 0.76
    assert settings.tavily_api_key is not None
    assert settings.tavily_api_key.get_secret_value() == "tavily-secret"
    assert "tavily-secret" not in repr(settings.tavily_api_key)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        (
            "BRUNO_EXPENSE_CLASSIFIER_MODE",
            "automatic",
            "BRUNO_EXPENSE_CLASSIFIER_MODE must be off, shadow, or apply",
        ),
        (
            "BRUNO_EXPENSE_CLASSIFICATION_THRESHOLD",
            "0",
            "BRUNO_EXPENSE_CLASSIFICATION_THRESHOLD must be greater than 0 and at most 1",
        ),
    ],
)
def test_invalid_expense_classifier_configuration_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        load_bruno_settings()
