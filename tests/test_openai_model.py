"""Tests for GPT-5.6 model selection and LangSmith environment wiring."""

import json
import os

from financial_agent.config import ReasoningEffort, Settings
from financial_agent.integrations.openai_budget_review import BudgetPreferenceReviewOutput
from financial_agent.integrations.openai_model import build_openai_chat_model
from financial_agent.observability import configure_langsmith_environment


def test_openai_model_uses_terra_responses_api_and_medium_reasoning() -> None:
    settings = Settings(_env_file=None, model_api_key="test-key")

    model = build_openai_chat_model(settings)

    assert model.model_name == "gpt-5.6-terra"
    assert model.use_responses_api is True
    assert model.reasoning == {"effort": ReasoningEffort.MEDIUM.value, "summary": "auto"}


def test_openai_model_rejects_blank_key() -> None:
    settings = Settings(_env_file=None, model_api_key="")

    try:
        build_openai_chat_model(settings)
    except ValueError as exc:
        assert "OPENAI_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected a blank OpenAI key to be rejected")


def test_budget_preference_review_schema_avoids_unsupported_regex_patterns() -> None:
    schema = json.dumps(BudgetPreferenceReviewOutput.model_json_schema())

    assert '"pattern"' not in schema


def test_langsmith_bridge_preserves_explicit_standard_environment(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("LANGSMITH_PROJECT", "explicit-project")
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,
        langsmith_tracing=True,
        langsmith_api_key="trace-key",
        langsmith_project="configured-project",
    )

    configure_langsmith_environment(settings)

    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_API_KEY"] == "trace-key"
    assert os.environ["LANGSMITH_PROJECT"] == "explicit-project"
