"""Debounced expense check-up scheduling and Telegram presentation."""

from decimal import Decimal
from typing import Any


def format_checkup_message(result: dict[str, Any]) -> str | None:
    """Return a user-facing alert only when the finance result is actionable."""

    if not result.get("should_notify"):
        return None
    lines = ["📊 Expense check-up"]
    for projection in result.get("projections", []):
        lines.append(
            f"⚠️ {projection['subcategory']} is projected ₪{projection['amount']} "
            f"over budget ({projection['percent']}%)."
        )
    for overspend in result.get("actual_overspends", []):
        lines.append(
            f"🚨 {overspend['subcategory']} is ₪{overspend['overspend']} over budget."
        )

    mutation = result.get("mutation", {})
    disposition = mutation.get("disposition")
    changes = result.get("budget_changes", [])
    if disposition in {"applied", "already_applied"} and changes:
        lines.append("✅ Rebalanced this month's budgets:")
        lines.extend(
            f"• {change['subcategory']}: ₪{change['before']} → ₪{change['after']}"
            for change in changes
        )
    elif disposition == "disabled" and changes:
        lines.append(
            "⚠️ A safe rebalance is available, but automatic budget adjustments "
            "are disabled in Financial Rules."
        )
    elif disposition in {"blocked", "failed_rolled_back", "partial_failure"}:
        lines.append(f"❌ Automatic rebudgeting did not complete: {mutation.get('reason')}")

    if result.get("savings_required"):
        savings_amount = result.get("savings_amount") or result.get(
            "unresolved_variable_deficit"
        )
        lines.append(
            "🆘 The spending plan is underfunded by "
            f"₪{savings_amount}. There is not enough "
            "adjustable budget within this month's income; pull this amount from savings."
        )
    elif "income_missing" in result.get("observations", []):
        lines.append("⚠️ Income is missing, so no automatic rebalance was allowed.")
    outlook = result.get("account_outlook")
    if isinstance(outlook, dict):
        projected = outlook.get("projected_balance_after_settlement")
        minimum = outlook.get("minimum_available_balance")
        available = outlook.get("available_above_minimum")
        if outlook.get("on_track") is False:
            shortfall = abs(Decimal(str(available or "0")))
            lines.append(
                "🚨 Projected bank balance after the next settlement is "
                f"₪{projected}, which is ₪{shortfall} below "
                f"the preferred ₪{minimum} minimum."
            )
        transfer = outlook.get("recommended_savings_transfer")
        if transfer not in {None, "0.00"}:
            lines.append(
                f"💰 Projected surplus is ₪{transfer}; move it to savings after "
                "the settlement clears."
            )
    return "\n".join(lines)


def is_urgent_checkup(result: dict[str, Any]) -> bool:
    """Reserve immediate interruption for actual or structurally dangerous states."""

    disposition = result.get("mutation", {}).get("disposition")
    outlook = result.get("account_outlook")
    extreme_account_shortfall = False
    if isinstance(outlook, dict) and outlook.get("on_track") is False:
        available = abs(Decimal(str(outlook.get("available_above_minimum") or "0")))
        threshold = Decimal(str(outlook.get("savings_sweep_threshold") or "0"))
        extreme_account_shortfall = threshold > 0 and available >= threshold
    return bool(
        result.get("actual_overspends")
        or result.get("savings_required")
        or "budgets_exceed_income" in result.get("observations", [])
        or disposition in {"blocked", "failed_rolled_back", "partial_failure"}
        or extreme_account_shortfall
    )
