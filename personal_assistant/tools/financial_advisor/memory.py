from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

_BUDGET_DATA_DIR = Path(__file__).resolve().parents[3] / "budget_data"
_DEFAULT_PROFILE = {
    "bank_account_balance": {
        "balance": None,
        "currency": "ILS",
        "updated_at": None,
        "notes": "",
    },
    "emergency_fund_months": 3,
    # Preferences for the Future Purchases / Vacations / Expenses planning.
    # Presented and updated through the get/update tools below, so Tal can
    # change the policy in conversation without code changes.
    "future_planning": {
        "vacations": {"min_planned_vacations": 1, "notes": ""},
        "future_expenses": {"default_monthly_saving_ils": 500.0, "max_saving_months": 3},
        "active_savings_goals": [],
    },
}


def _data_dir() -> Path:
    # FINANCIAL_ADVISOR_DATA_DIR override keeps tests off the real profile.
    override = os.getenv("FINANCIAL_ADVISOR_DATA_DIR")
    return Path(override) if override else _BUDGET_DATA_DIR


def _profile_path() -> Path:
    return _data_dir() / "financial_advisor_profile.json"


def _recommendations_path() -> Path:
    return _data_dir() / "financial_recommendations.json"


def _read_profile() -> dict[str, Any]:
    profile_path = _profile_path()
    if not profile_path.exists():
        return json.loads(json.dumps(_DEFAULT_PROFILE))
    try:
        content = profile_path.read_text(encoding="utf-8").strip()
        if not content:
            return json.loads(json.dumps(_DEFAULT_PROFILE))
        profile = json.loads(content)
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(_DEFAULT_PROFILE))
    merged = json.loads(json.dumps(_DEFAULT_PROFILE))
    merged.update(profile)
    merged["bank_account_balance"].update(profile.get("bank_account_balance", {}))
    # Deep-merge future_planning so new default keys appear for old profiles.
    merged_planning = json.loads(json.dumps(_DEFAULT_PROFILE["future_planning"]))
    stored_planning = profile.get("future_planning", {})
    for section in ("vacations", "future_expenses"):
        merged_planning[section].update(stored_planning.get(section, {}))
    merged_planning["active_savings_goals"] = stored_planning.get("active_savings_goals", [])
    merged["future_planning"] = merged_planning
    return merged


def _write_profile(profile: dict[str, Any]) -> None:
    profile_path = _profile_path()
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


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


@tool
def get_future_planning_preferences() -> dict[str, Any]:
    """Return the remembered future-planning preferences: vacation policy
    (minimum planned vacations), the future-expense saving rule (default ILS
    per month and max saving months), and the savings goals currently being
    worked toward. Use this to present the current preferences to the user."""
    return _read_profile()["future_planning"]


@tool
def update_vacation_preferences(min_planned_vacations: int | None = None, notes: str | None = None) -> dict[str, Any]:
    """Update the remembered vacation-planning preferences. Only call when the
    user explicitly asks to change them. Omit an argument to leave it as is."""
    profile = _read_profile()
    vacations = profile["future_planning"]["vacations"]
    if min_planned_vacations is not None:
        if min_planned_vacations < 0:
            raise ValueError("min_planned_vacations cannot be negative.")
        vacations["min_planned_vacations"] = int(min_planned_vacations)
    if notes is not None:
        vacations["notes"] = notes
    _write_profile(profile)
    return {"ok": True, "vacations": vacations}


@tool
def update_future_expense_saving_rule(
    default_monthly_saving_ils: float | None = None,
    max_saving_months: int | None = None,
) -> dict[str, Any]:
    """Update the remembered future-expense saving rule. Only call when the
    user explicitly asks to change it. Omit an argument to leave it as is."""
    profile = _read_profile()
    rule = profile["future_planning"]["future_expenses"]
    if default_monthly_saving_ils is not None:
        if default_monthly_saving_ils <= 0:
            raise ValueError("default_monthly_saving_ils must be positive.")
        rule["default_monthly_saving_ils"] = round(float(default_monthly_saving_ils), 2)
    if max_saving_months is not None:
        if max_saving_months < 1:
            raise ValueError("max_saving_months must be at least 1.")
        rule["max_saving_months"] = int(max_saving_months)
    _write_profile(profile)
    return {"ok": True, "future_expenses": rule}


@tool
def set_active_savings_goal(
    name: str,
    source: str = "future_purchases",
    monthly_amount: float | None = None,
    strategy: str = "saving",
    notes: str = "",
) -> dict[str, Any]:
    """Remember that the user is actively working toward a goal (saving for a
    purchase/vacation, or planning to over-budget for it in coming months).

    `source` is 'future_purchases', 'future_vacations', or 'future_expenses';
    `strategy` is 'saving' or 'over_budget'. Upserts by goal name.
    """
    if source not in {"future_purchases", "future_vacations", "future_expenses"}:
        raise ValueError("source must be future_purchases, future_vacations, or future_expenses.")
    if strategy not in {"saving", "over_budget"}:
        raise ValueError("strategy must be 'saving' or 'over_budget'.")
    profile = _read_profile()
    goals = profile["future_planning"]["active_savings_goals"]
    goal = {
        "name": name,
        "source": source,
        "monthly_amount": round(float(monthly_amount), 2) if monthly_amount is not None else None,
        "strategy": strategy,
        "notes": notes,
        "since": datetime.now().date().isoformat(),
    }
    goals[:] = [existing for existing in goals if existing.get("name") != name]
    goals.append(goal)
    _write_profile(profile)
    return {"ok": True, "goal": goal, "active_savings_goals": goals}


@tool
def remove_active_savings_goal(name: str) -> dict[str, Any]:
    """Forget an active savings goal (e.g. after it was bought or abandoned)."""
    profile = _read_profile()
    goals = profile["future_planning"]["active_savings_goals"]
    remaining = [goal for goal in goals if goal.get("name") != name]
    if len(remaining) == len(goals):
        return {"ok": False, "error": f"No active savings goal named '{name}'."}
    profile["future_planning"]["active_savings_goals"] = remaining
    _write_profile(profile)
    return {"ok": True, "active_savings_goals": remaining}


def _read_recommendations() -> list[dict[str, Any]]:
    recommendations_path = _recommendations_path()
    if not recommendations_path.exists():
        return []
    try:
        content = recommendations_path.read_text(encoding="utf-8").strip()
        return json.loads(content) if content else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_recommendations(recommendations: list[dict[str, Any]]) -> None:
    recommendations_path = _recommendations_path()
    recommendations_path.parent.mkdir(parents=True, exist_ok=True)
    recommendations_path.write_text(
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
