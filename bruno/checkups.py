"""Debounced expense check-up scheduling and Telegram presentation."""

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
    return "\n".join(lines)
