"""Tavily adapter for minimal public merchant context."""

from typing import Any, Protocol, cast

from pydantic import SecretStr
from tavily import AsyncTavilyClient  # type: ignore[import-untyped]

from financial_agent.services.expense_classification import SearchEvidence

_MAX_MERCHANT_CHARS = 200
_MAX_TITLE_CHARS = 200
_MAX_URL_CHARS = 500
_MAX_CONTENT_CHARS = 1_000
_MAX_RESULTS = 3


class TavilyAsyncSearchClient(Protocol):
    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]: ...


class TavilyMerchantSearch:
    """Search only a merchant name and return bounded, untrusted snippets."""

    def __init__(
        self,
        api_key: SecretStr | str,
        *,
        client: TavilyAsyncSearchClient | None = None,
    ) -> None:
        key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        self._client = client or cast(
            TavilyAsyncSearchClient,
            AsyncTavilyClient(api_key=key, client_name="bruno-expense-classifier"),
        )

    async def search(self, merchant: str) -> tuple[SearchEvidence, ...]:
        cleaned = " ".join(merchant.split())[:_MAX_MERCHANT_CHARS]
        if not cleaned:
            return ()
        response = await self._client.search(
            f'"{cleaned}" Israel business merchant category',
            search_depth="basic",
            topic="general",
            max_results=_MAX_RESULTS,
            include_answer=False,
            include_raw_content=False,
            include_images=False,
            timeout=15,
        )
        raw_results = response.get("results")
        if not isinstance(raw_results, list):
            return ()
        evidence: list[SearchEvidence] = []
        for item in raw_results[:_MAX_RESULTS]:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            url = item.get("url")
            content = item.get("content")
            if not isinstance(title, str):
                continue
            if not isinstance(url, str):
                continue
            if not isinstance(content, str):
                continue
            evidence.append(
                SearchEvidence(
                    title=title.strip()[:_MAX_TITLE_CHARS],
                    url=url.strip()[:_MAX_URL_CHARS],
                    content=content.strip()[:_MAX_CONTENT_CHARS],
                )
            )
        return tuple(evidence)
