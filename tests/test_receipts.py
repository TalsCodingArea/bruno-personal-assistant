"""Receipt extraction tolerates model confidence formats."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bruno.receipts import _usable, extract_receipt


@pytest.mark.parametrize("confidence", ["High", " HIGH ", 0.95, "0.95"])
def test_text_receipt_accepts_high_confidence_without_vision_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, confidence: object
) -> None:
    pdf = tmp_path / "receipt.pdf"
    pdf.write_bytes(b"%PDF-test")
    client = Mock()
    client.files.create.return_value = SimpleNamespace(id="file-1")
    client.responses.create.return_value = SimpleNamespace(
        output_text=json.dumps(
            {
                "vendor": "Shop",
                "confidence": confidence,
                "total_amount": 42,
                "category": "Groceries",
            }
        )
    )
    monkeypatch.setattr("bruno.receipts.OpenAI", lambda: client)
    monkeypatch.setattr("bruno.receipts._pdf_text_probe", lambda _: (True, "receipt text"))
    result = extract_receipt(pdf, category_options=("Groceries",), model="test")
    assert result["vendor"] == "Shop"
    assert result["total_amount"] == 42
    assert result["source_pdf_type"] == "text_pdf"
    client.responses.create.assert_called_once()
    client.files.delete.assert_called_once_with("file-1")


@pytest.mark.parametrize("confidence", [None, "Low", "nonsense", [], {}, True, "NaN", "inf", 2, -1])
def test_invalid_or_low_confidence_requests_fallback(confidence: object) -> None:
    assert not _usable({"vendor": "Shop", "confidence": confidence})
