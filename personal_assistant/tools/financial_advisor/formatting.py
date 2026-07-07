from __future__ import annotations

from typing import Any


def ils(value: Any) -> str:
    if value is None:
        return "unknown"
    try:
        return f"{float(value):,.0f} ILS"
    except (TypeError, ValueError):
        return str(value)


def compact_reasons(reasons: list[str], limit: int = 2) -> str:
    return " ".join(reason for reason in reasons[:limit] if reason)
