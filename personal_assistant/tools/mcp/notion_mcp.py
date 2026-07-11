"""Notion MCP fallback tools.

Bruno's day-to-day Notion work (expenses, budgets, future purchases/vacations,
financial advisor state) goes through the purpose-built tools in
tools/notion_tools.py and tools/financial_advisor/notion_tools.py. Those know
our schema conventions -- e.g. a Budget DB row must be named exactly like its
sub-category and carry the right Financial Summary relation for its month.
A generic tool has no idea that convention exists, so a generic *write* can
silently corrupt the formulas that tie budgeting together.

This module exists for the other case: the agent gets stuck because none of
the dedicated tools cover what was asked (an ad-hoc lookup in some database
we haven't built a tool for yet, "when did I last touch page X", etc.). For
that we spin up Notion's official local MCP server
(https://github.com/makenotion/notion-mcp-server) as a subprocess over stdio,
authenticated with the same NOTION_API_KEY Bruno already uses.

Policy: only read-only tools are exposed to the agent. Anything that can
create, update, move, or delete content is filtered out here, even though the
MCP server technically offers it -- writes must go through the dedicated
tools above. See system_prompt.py for the matching instruction to the model.

IMPORTANT -- verify after first run: notion-mcp-server generates its tool
names from Notion's OpenAPI spec, and that naming has changed across major
versions (see the README's v2.0.0 migration notes). READ_ONLY_TOOL_NAMES below
is a best-effort allowlist. This filter fails *closed*: a tool must match a
name in the set to be exposed, so if the installed server uses different
names, the agent will simply get zero fallback tools (safe) rather than
accidentally exposing a write tool (unsafe). Run
`python -m personal_assistant.tools.mcp.notion_mcp` once after installing to
print every tool the server actually advertises and confirm/adjust the set
below.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger("personal-assistant.mcp.notion")

# Pinned so an upstream major bump (which has renamed tools before -- see the
# v2.0.0 migration notes) can't silently change the tool set under us. The
# fail-closed allowlist below means a rename would quietly drop ALL fallback
# tools; with a pin, upgrades are a deliberate step: bump the version, rerun
# `python -m personal_assistant.tools.mcp.notion_mcp`, adjust the allowlist.
_NOTION_MCP_SERVER_VERSION = "2.4.1"

# How long to wait for the npx subprocess to start and list its tools. The
# first ever run downloads the package, so this is generous.
_CONNECT_TIMEOUT_SECONDS = 60.0

# After a failed connection attempt, don't retry for this long. Failures are
# NOT cached permanently (a transient npx/network hiccup at startup shouldn't
# disable the fallback for the whole process lifetime), but retrying on every
# new chat would spawn an npx subprocess each time -- hence the cooldown.
_RETRY_COOLDOWN_SECONDS = 300.0

# Best-effort allowlist -- see module docstring. Update after running the
# inspection entrypoint below against your installed server version.
READ_ONLY_TOOL_NAMES: set[str] = {
    "post-search",
    "retrieve-a-page",
    "retrieve-a-block",
    "retrieve-block-children",
    "retrieve-a-database",
    "retrieve-a-data-source",
    "query-data-source",
    "list-data-source-templates",
    "retrieve-a-comment",
    "retrieve-a-user",
    "list-all-users",
    "retrieve-a-token-s-bot-user",
}

_MCP_SERVER_CONFIG = {
    "notion": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", f"@notionhq/notion-mcp-server@{_NOTION_MCP_SERVER_VERSION}"],
        "env": {},  # filled in lazily so we read NOTION_API_KEY at call time
    }
}

_client: MultiServerMCPClient | None = None
_tools_cache: list[Any] | None = None
_lock = asyncio.Lock()
_last_failure_monotonic: float | None = None


def _server_config() -> dict[str, Any]:
    token = os.getenv("NOTION_API_KEY")
    if not token:
        raise ValueError("Missing NOTION_API_KEY in .env -- required for the Notion MCP fallback tools")

    config = {**_MCP_SERVER_CONFIG["notion"]}
    config["env"] = {
        "OPENAPI_MCP_HEADERS": (
            '{"Authorization": "Bearer %s", "Notion-Version": "2025-09-03"}' % token
        )
    }
    return {"notion": config}


async def get_notion_mcp_tools() -> list[Any]:
    """Return the cached, read-only-filtered set of Notion MCP tools.

    Loads and connects to the local notion-mcp-server subprocess on first
    call; subsequent calls reuse the cached tool list. Returns an empty list
    (and logs a warning) instead of raising if the server can't be reached,
    so a broken MCP fallback never takes down the whole agent. Failures are
    retried after _RETRY_COOLDOWN_SECONDS rather than cached forever.
    """
    global _client, _tools_cache, _last_failure_monotonic

    if _tools_cache is not None:
        return _tools_cache

    async with _lock:
        if _tools_cache is not None:
            return _tools_cache

        if (
            _last_failure_monotonic is not None
            and time.monotonic() - _last_failure_monotonic < _RETRY_COOLDOWN_SECONDS
        ):
            return []

        try:
            _client = MultiServerMCPClient(_server_config())
            all_tools = await asyncio.wait_for(
                _client.get_tools(), timeout=_CONNECT_TIMEOUT_SECONDS
            )
        except Exception:
            logger.exception(
                "Failed to connect to the Notion MCP server; fallback tools disabled "
                "for the next %.0f seconds, then retried",
                _RETRY_COOLDOWN_SECONDS,
            )
            _last_failure_monotonic = time.monotonic()
            return []

        _last_failure_monotonic = None

        allowed = [t for t in all_tools if t.name in READ_ONLY_TOOL_NAMES]
        skipped = sorted(t.name for t in all_tools if t.name not in READ_ONLY_TOOL_NAMES)

        logger.info("Notion MCP: exposing %d read-only tools: %s", len(allowed), sorted(t.name for t in allowed))
        if skipped:
            logger.info("Notion MCP: filtered out %d non-allowlisted tools: %s", len(skipped), skipped)

        _tools_cache = allowed
        return _tools_cache


async def _print_discovered_tools() -> None:
    """One-off inspection helper -- see module docstring."""
    client = MultiServerMCPClient(_server_config())
    tools = await client.get_tools()
    print(f"notion-mcp-server advertises {len(tools)} tools:\n")
    for tool in sorted(tools, key=lambda t: t.name):
        flag = "READ-ONLY (allowed)" if tool.name in READ_ONLY_TOOL_NAMES else "NOT in allowlist"
        print(f"  [{flag}] {tool.name} -- {tool.description}")


if __name__ == "__main__":
    asyncio.run(_print_discovered_tools())
