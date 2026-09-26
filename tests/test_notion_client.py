"""HTTP-boundary tests for Notion pagination and request shape."""

import asyncio

import httpx
from financial_agent.integrations.notion import NotionClient
from notion_client import AsyncClient as NotionSDKAsyncClient


def test_query_all_uses_data_source_endpoint_and_paginates() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = request.read().decode()
        if "cursor-1" in body:
            return httpx.Response(
                200,
                json={"results": [{"id": "second"}], "has_more": False, "next_cursor": None},
            )
        return httpx.Response(
            200,
            json={"results": [{"id": "first"}], "has_more": True, "next_cursor": "cursor-1"},
        )

    async def run() -> list[dict[str, object]]:
        sdk = NotionSDKAsyncClient(
            auth="secret",
            notion_version="2026-03-11",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        async with NotionClient("secret", client=sdk) as client:
            return await client.query_all("source-id", filter_properties=["Final", "Date"])

    result = asyncio.run(run())

    assert [item["id"] for item in result] == ["first", "second"]
    assert len(requests) == 2
    assert requests[0].url.path == "/v1/data_sources/source-id/query"
    assert requests[0].headers["notion-version"] == "2026-03-11"
    assert requests[0].url.params.get_list("filter_properties") == ["Final", "Date"]
