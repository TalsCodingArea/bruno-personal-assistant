"""Convert domain dataclasses to compact JSON-safe tool results."""

from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal
from typing import TypeAlias

JsonValue: TypeAlias = (
    str | int | float | bool | list["JsonValue"] | dict[str, "JsonValue"] | None
)


def jsonable(value: object) -> JsonValue:
    if is_dataclass(value) and not isinstance(value, type):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    raise TypeError(f"Unsupported tool result value: {type(value).__name__}")
