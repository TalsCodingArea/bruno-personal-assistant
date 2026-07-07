from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

_BUDGET_DATA_DIR = Path(__file__).resolve().parents[2] / "budget_data"
_PROFILE_PATH = _BUDGET_DATA_DIR / "financial_advisor_profile.json"
_RECOMMENDATIONS_PATH = _BUDGET_DATA_DIR / "financial_recommendations.json"
_DEFAULT_PROFILE = {
    "bank_account_balance": {
        "balance": None,
        "currency": "ILS",
        "updated_at": None,
        "notes": "",
    },
    "emergency_fund_months": 3,
}


def _read_profile() -> dict[str, Any]:
    if not _PROFILE_PATH.exists():
        return json.loads(json.dumps(_DEFAULT_PROFILE))
    try:
        content = _PROFILE_PATH.read_text(encoding="utf-8").strip()
        if not content:
            return json.loads(json.dumps(_DEFAULT_PROFILE))
        profile = json.loads(content)
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(_DEFAULT_PROFILE))
    merged = json.loads(json.dumps(_DEFAULT_PROFILE))
    merged.update(profile)
    merged["bank_account_balance"].update(profile.get("bank_account_balance", {}))
    return merged


def _write_profile(profile: dict[str, Any]) -> None:
    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


def load_financial_profile() -> dict[str, Any]:
    return _read_profile()


@tool
def get_financial_profile() -> dict[str, Any]:
    """Return the local financial advisor profile, including bank balance and emergency fund policy."""
    return _read_profile()


@tool
def get_current_bank_balance() -> dict[str, Any]:
    """Return the locally remembered current bank account balance."""
    return _read_profile()["bank_account_balance"]


@tool
def update_bank_account_balance(balance: float, currency: str = "ILS", notes: str = "") -> dict[str, Any]:
    """Update the locally remembered current bank account balance."""
    profile = _read_profile()
    profile["bank_account_balance"] = {
        "balance": round(float(balance), 2),
        "currency": currency,
        "updated_at": datetime.now().date().isoformat(),
        "notes": notes,
    }
    _write_profile(profile)
    return {"ok": True, "bank_account_balance": profile["bank_account_balance"]}


@tool
def update_emergency_fund_months(months: float) -> dict[str, Any]:
    """Update how many months of expenses should be kept in the bank account."""
    if months <= 0:
        raise ValueError("months must be positive.")
    profile = _read_profile()
    profile["emergency_fund_months"] = float(months)
    _write_profile(profile)
    return {"ok": True, "emergency_fund_months": profile["emergency_fund_months"]}


def _read_recommendations() -> list[dict[str, Any]]:
    if not _RECOMMENDATIONS_PATH.exists():
        return []
    try:
        content = _RECOMMENDATIONS_PATH.read_text(encoding="utf-8").strip()
        return json.loads(content) if content else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_recommendations(recommendations: list[dict[str, Any]]) -> None:
    _RECOMMENDATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RECOMMENDATIONS_PATH.write_text(
        json.dumps(recommendations, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@tool
def log_financial_recommendation(
    recommendation: str,
    recommendation_type: str = "General",
    numbers_used: str = "",
) -> dict[str, Any]:
    """Persist a financial recommendation to local advisor memory."""
    recommendations = _read_recommendations()
    entry = {
        "id": len(recommendations) + 1,
        "date": datetime.now().date().isoformat(),
        "type": recommendation_type,
        "recommendation": recommendation,
        "numbers_used": numbers_used,
        "status": "Suggested",
    }
    recommendations.append(entry)
    _write_recommendations(recommendations)
    return {"ok": True, "recommendation": entry}


@tool
def get_financial_recommendations(status: str = "") -> dict[str, Any]:
    """Return locally remembered financial recommendations, optionally filtered by status."""
    recommendations = _read_recommendations()
    if status:
        recommendations = [item for item in recommendations if item.get("status") == status]
    return {"recommendations": recommendations}


@tool
def update_financial_recommendation_status(recommendation_id: int, status: str) -> dict[str, Any]:
    """Update the status of a remembered financial recommendation (Suggested/Accepted/Rejected/Done)."""
    recommendations = _read_recommendations()
    for entry in recommendations:
        if entry.get("id") == recommendation_id:
            entry["status"] = status
            _write_recommendations(recommendations)
            return {"ok": True, "recommendation": entry}
    return {"ok": False, "error": f"No recommendation with id {recommendation_id}."}
