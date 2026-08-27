"""OpenAI chat-model construction at the external integration boundary."""

from langchain_openai import ChatOpenAI

from app.config import Settings


def build_openai_chat_model(settings: Settings) -> ChatOpenAI:
    """Build the project's GPT-5.6 Responses API model from typed settings."""

    if (
        settings.model_api_key is None
        or not settings.model_api_key.get_secret_value().strip()
    ):
        raise ValueError("OPENAI_API_KEY is required")
    return ChatOpenAI(
        model=settings.model_name,
        api_key=settings.model_api_key,
        use_responses_api=True,
        output_version="responses/v1",
        reasoning={
            "effort": settings.model_reasoning_effort.value,
            "summary": "auto",
        },
        max_retries=2,
        timeout=60.0,
    )
