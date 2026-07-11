"""MCP-backed tools.

Unlike personal_assistant/tools/notion_tools.py and
personal_assistant/tools/financial_advisor/notion_tools.py — which are
purpose-built, schema-aware tools for Bruno's known Notion workflows — the
tools in this package wrap a general-purpose MCP server. They exist as a
fallback for requests the dedicated tools don't cover, not as a replacement
for them. See notion_mcp.py for the read-only policy this enforces.
"""
