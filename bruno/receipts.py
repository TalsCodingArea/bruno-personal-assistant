"""Receipt PDF extraction retained from Bruno without finance persistence."""

import base64
import io
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from openai import OpenAI

_PRINTED_DATE_RE = re.compile(
    r"(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})(?!\d)"
)
_MAX_RECEIPT_AGE = timedelta(days=366)


def extract_receipt(
    pdf_path: Path,
    *,
    category_options: tuple[str, ...],
    model: str,
) -> dict[str, Any]:
    """Extract one receipt with PDF input and a vision fallback for scans."""

    pdf_bytes = pdf_path.read_bytes()
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ValueError("Receipt is not a valid PDF")
    client = OpenAI()
    prompt = _receipt_prompt(category_options)
    has_text, sample = _pdf_text_probe(pdf_bytes)
    parsed: dict[str, Any] | None = None
    source_type = "text_pdf" if has_text else "scanned_pdf"

    if has_text:
        buffer = io.BytesIO(pdf_bytes)
        buffer.name = pdf_path.name
        uploaded = client.files.create(file=buffer, purpose="user_data")
        try:
            text_input: Any = [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_file", "file_id": uploaded.id},
                    ],
                }
            ]
            response = client.responses.create(
                model=model,
                input=text_input,
            )
            parsed = _parse_json(response.output_text)
        finally:
            client.files.delete(uploaded.id)

    if not _usable(parsed):
        images = _pdf_images(pdf_bytes)
        image_input: Any = [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    *(
                        {"type": "input_image", "image_url": image}
                        for image in images
                    ),
                ],
            }
        ]
        response = client.responses.create(
            model=model,
            input=image_input,
        )
        parsed = _parse_json(response.output_text)
        source_type = "scanned_pdf"

    if parsed is None:
        raise RuntimeError("Receipt extraction returned no result")
    parsed["category"] = _normalize_category(
        parsed.get("category"), category_options
    )
    parsed["source_pdf_type"] = source_type
    parsed["text_probe_sample"] = sample
    return parsed


def receipt_expense_fields(
    receipt: dict[str, Any],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Map Bruno's receipt labels to the finance expense schema."""

    current_date = today or datetime.now(ZoneInfo("Asia/Jerusalem")).date()
    category = str(receipt.get("category") or "Uncategorized")
    mappings = {
        "Groceries": (["Home 🏡"], ["Groceries 🛒"]),
        "EV": (["Car 🚗"], ["Electric 🔋"]),
        "Bills": (["Home 🏡"], ["Bills 🧾"]),
    }
    categories, subcategories = mappings.get(
        category, (["Uncategorized"], [])
    )
    return {
        "description": str(receipt.get("vendor") or "Receipt"),
        "amount": str(receipt.get("total_amount")),
        "occurred_on": _reasonable_receipt_date(receipt, today=current_date),
        "category": categories,
        "subcategory": subcategories,
        "payment_method": "Credit",
        "expense_type": "Need",
    }


def _receipt_prompt(category_options: tuple[str, ...]) -> str:
    options = ", ".join((*category_options, "Unrecognized"))
    return (
        "Extract this Hebrew or English receipt. Return strict JSON only with keys: "
        "vendor, total_amount, currency, category, language, country, date_text, "
        "confidence, reasoning, date. Identify the receipt's country from its language, "
        "currency, tax identifiers, and merchant details. Copy the printed transaction date "
        "exactly into date_text before interpreting it. Israeli numeric receipt dates use "
        "DD/MM/YY or DD/MM/YYYY, never YY/MM/DD. date must be ISO 8601 or null. "
        "total_amount must be the amount after tax. "
        f"category must be one of: {options}."
    )


def _reasonable_receipt_date(receipt: dict[str, Any], *, today: date) -> str | None:
    """Prefer a locale-aware printed date and reject implausible receipt dates."""

    printed = _parse_printed_date(
        receipt.get("date_text"),
        day_first=_is_israeli_receipt(receipt),
        today=today,
    )
    if printed is not None and _is_reasonable_receipt_date(printed, today=today):
        return printed.isoformat()

    extracted = _parse_iso_date(receipt.get("date"))
    if extracted is not None and _is_reasonable_receipt_date(extracted, today=today):
        return extracted.isoformat()

    if extracted is not None and _is_israeli_receipt(receipt):
        corrected = _correct_reversed_israeli_short_date(extracted, today=today)
        if corrected is not None and _is_reasonable_receipt_date(corrected, today=today):
            return corrected.isoformat()
    return None


def _parse_printed_date(value: Any, *, day_first: bool, today: date) -> date | None:
    if not isinstance(value, str):
        return None
    match = _PRINTED_DATE_RE.search(value)
    if match is None:
        return None
    first, second, year_text = (int(part) for part in match.groups())
    if day_first or first > 12:
        day, month = first, second
    else:
        month, day = first, second
    year = _expand_short_year(year_text, today=today)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _expand_short_year(value: str | int, *, today: date) -> int:
    parsed = int(value)
    if parsed >= 100:
        return parsed
    century = today.year - (today.year % 100)
    candidates = (century - 100 + parsed, century + parsed, century + 100 + parsed)
    return min(candidates, key=lambda year: abs(year - today.year))


def _is_israeli_receipt(receipt: dict[str, Any]) -> bool:
    country = str(receipt.get("country") or "").strip().casefold()
    language = str(receipt.get("language") or "").strip().casefold()
    currency = str(receipt.get("currency") or "").strip().upper()
    return (
        country in {"israel", "il", "isr", "ישראל"}
        or language in {"he", "heb", "hebrew", "עברית"}
        or currency in {"ILS", "NIS", "₪"}
    )


def _correct_reversed_israeli_short_date(value: date, *, today: date) -> date | None:
    """Correct an observed DD/MM/YY -> YY-MM-DD model transposition."""

    intended_day = value.year % 100
    intended_year = _expand_short_year(value.day, today=today)
    try:
        return date(intended_year, value.month, intended_day)
    except ValueError:
        return None


def _is_reasonable_receipt_date(value: date, *, today: date) -> bool:
    return today - _MAX_RECEIPT_AGE <= value <= today


def _pdf_text_probe(pdf_bytes: bytes) -> tuple[bool, str]:
    import fitz  # type: ignore[import-untyped]

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_text: list[str] = []
        for index in range(min(2, len(document))):
            extracted = document.load_page(index).get_text("text")
            if isinstance(extracted, str):
                page_text.append(extracted.strip())
        text = "\n".join(page_text).strip()
    finally:
        document.close()
    return len(text) >= 20, text[:500]


def _pdf_images(pdf_bytes: bytes) -> list[str]:
    import fitz

    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        matrix = fitz.Matrix(220 / 72, 220 / 72)
        return [
            "data:image/png;base64,"
            + base64.b64encode(
                document.load_page(index)
                .get_pixmap(matrix=matrix, alpha=False)
                .tobytes("png")
            ).decode("ascii")
            for index in range(min(2, len(document)))
        ]
    finally:
        document.close()


def _parse_json(raw: str) -> dict[str, Any]:
    text = re.sub(r"^```json\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match is None:
            raise ValueError("Receipt model did not return JSON") from None
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Receipt model returned a non-object JSON value")
    return value


def _usable(value: dict[str, Any] | None) -> bool:
    if value is None or value.get("vendor") is None:
        return False
    return float(value.get("confidence") or 0) >= 0.5


def _normalize_category(value: Any, options: tuple[str, ...]) -> str:
    allowed = {item.casefold(): item for item in options}
    if isinstance(value, str):
        return allowed.get(value.strip().casefold(), "Unrecognized")
    return "Unrecognized"
