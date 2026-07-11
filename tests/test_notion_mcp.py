from __future__ import annotations

import unittest
from dataclasses import dataclass
from unittest import mock

from personal_assistant.tools.mcp import notion_mcp


@dataclass
class FakeTool:
    name: str
    description: str = ""


class FakeMCPClient:
    """Stands in for MultiServerMCPClient without spawning npx."""

    def __init__(self, tools: list[FakeTool] | None = None, error: Exception | None = None) -> None:
        self._tools = tools or []
        self._error = error

    async def get_tools(self) -> list[FakeTool]:
        if self._error is not None:
            raise self._error
        return self._tools


def _reset_module_state() -> None:
    notion_mcp._client = None
    notion_mcp._tools_cache = None
    notion_mcp._last_failure_monotonic = None


class NotionMcpToolLoadingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _reset_module_state()
        self.addCleanup(_reset_module_state)
        self.env_patcher = mock.patch.dict("os.environ", {"NOTION_API_KEY": "ntn_test"})
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)

    async def test_filters_to_read_only_allowlist(self) -> None:
        advertised = [
            FakeTool("post-search"),
            FakeTool("retrieve-a-page"),
            FakeTool("create-a-page"),  # write -- must be filtered out
            FakeTool("delete-a-block"),  # write -- must be filtered out
        ]
        with mock.patch.object(
            notion_mcp, "MultiServerMCPClient", return_value=FakeMCPClient(advertised)
        ):
            tools = await notion_mcp.get_notion_mcp_tools()

        self.assertEqual(sorted(t.name for t in tools), ["post-search", "retrieve-a-page"])

    async def test_success_is_cached_and_client_not_recreated(self) -> None:
        factory = mock.Mock(return_value=FakeMCPClient([FakeTool("post-search")]))
        with mock.patch.object(notion_mcp, "MultiServerMCPClient", factory):
            first = await notion_mcp.get_notion_mcp_tools()
            second = await notion_mcp.get_notion_mcp_tools()

        self.assertIs(first, second)
        self.assertEqual(factory.call_count, 1)

    async def test_connection_failure_returns_empty_without_raising(self) -> None:
        factory = mock.Mock(return_value=FakeMCPClient(error=RuntimeError("npx exploded")))
        with mock.patch.object(notion_mcp, "MultiServerMCPClient", factory):
            tools = await notion_mcp.get_notion_mcp_tools()

        self.assertEqual(tools, [])

    async def test_failure_respects_cooldown_then_retries(self) -> None:
        failing = mock.Mock(return_value=FakeMCPClient(error=RuntimeError("boom")))
        with mock.patch.object(notion_mcp, "MultiServerMCPClient", failing):
            self.assertEqual(await notion_mcp.get_notion_mcp_tools(), [])

        # Within the cooldown window: no new connection attempt, still empty.
        recovering = mock.Mock(return_value=FakeMCPClient([FakeTool("post-search")]))
        with mock.patch.object(notion_mcp, "MultiServerMCPClient", recovering):
            self.assertEqual(await notion_mcp.get_notion_mcp_tools(), [])
            self.assertEqual(recovering.call_count, 0)

            # After the cooldown expires, the next call retries and succeeds.
            notion_mcp._last_failure_monotonic -= notion_mcp._RETRY_COOLDOWN_SECONDS + 1
            tools = await notion_mcp.get_notion_mcp_tools()

        self.assertEqual([t.name for t in tools], ["post-search"])
        self.assertEqual(recovering.call_count, 1)

    async def test_missing_api_key_is_handled_as_failure(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("NOTION_API_KEY", None)
            tools = await notion_mcp.get_notion_mcp_tools()

        self.assertEqual(tools, [])
        self.assertIsNotNone(notion_mcp._last_failure_monotonic)
