"""Telegram MarkdownV2 helpers retained from bruno."""

import re
from typing import Any

from telegram.helpers import escape_markdown

_ENTITY_RE = re.compile(
    r"(?P<pre>```[\s\S]*?```)"
    r"|(?P<code>`[^`\n]+`)"
    r"|(?P<link>\[[^\]\n]+\]\([^)]+\))"
    r"|(?P<bold>\*(?:[^*\n]|\\\*)+\*)"
    r"|(?P<italic>_(?:[^_\n]|\\_)+_)"
)


def markdown_v2_safe(text: Any, *, preserve_formatting: bool = False) -> str:
    value = _convert_headings(str(text or ""))
    if not preserve_formatting:
        return escape_markdown(value, version=2)
    result: list[str] = []
    cursor = 0
    for match in _ENTITY_RE.finditer(value):
        if match.start() > cursor:
            result.append(escape_markdown(value[cursor : match.start()], version=2))
        token = match.group(0)
        kind = match.lastgroup
        if kind == "pre":
            result.append(
                "```" + escape_markdown(token[3:-3], version=2, entity_type="pre") + "```"
            )
        elif kind == "code":
            result.append(
                "`" + escape_markdown(token[1:-1], version=2, entity_type="code") + "`"
            )
        elif kind == "link":
            label, url = token[1:].split("](", 1)
            result.append(
                f"[{markdown_v2_safe(label, preserve_formatting=True)}]"
                f"({escape_markdown(url[:-1], version=2, entity_type='text_link')})"
            )
        else:
            delimiter = "*" if kind == "bold" else "_"
            result.append(
                delimiter
                + markdown_v2_safe(token[1:-1], preserve_formatting=True)
                + delimiter
            )
        cursor = match.end()
    result.append(escape_markdown(value[cursor:], version=2))
    return "".join(result)


def _convert_headings(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^#{1,6}\s+(.+)", line)
        lines.append(f"*{heading.group(1).strip()}*" if heading else line)
    return "\n".join(lines)
