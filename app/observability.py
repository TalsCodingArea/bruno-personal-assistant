"""Bridge typed project settings to the environment read by LangSmith tracing."""

import os

from app.config import Settings


def configure_langsmith_environment(settings: Settings) -> None:
    """Set standard LangSmith variables without overwriting explicit process values."""

    os.environ.setdefault(
        "LANGSMITH_TRACING", "true" if settings.langsmith_tracing else "false"
    )
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    if settings.langsmith_api_key is not None:
        os.environ.setdefault(
            "LANGSMITH_API_KEY", settings.langsmith_api_key.get_secret_value()
        )
