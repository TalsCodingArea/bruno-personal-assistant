"""Personal-chat completion report for successful bank Excel imports."""

from decimal import Decimal
from typing import Any


def format_bank_import_evaluation(
    import_result: dict[str, Any], evaluation: dict[str, Any]
) -> str:
    """Render a complete import receipt plus grounded cash and budget health."""

    status = import_result.get("status")
    icon = "⚠️" if status == "partial" else "✅"
    lines = [
        f"{icon} Bank Excel analysis finished",
        (
            f"Processed {import_result.get('files', 0)} file(s) and "
            f"{import_result.get('transactions', 0)} transaction(s): "
            f"{import_result.get('created', 0)} created, "
            f"{import_result.get('skipped', 0)} already known."
        ),
    ]
    conflicts = int(import_result.get("conflicts", 0) or 0)
    if conflicts:
        lines.append(
            f"Review {conflicts} conflict(s); their source file was kept and the "
            "evaluation may be incomplete."
        )

    outlook = evaluation.get("account_outlook")
    if isinstance(outlook, dict):
        movement = outlook.get("latest_movement")
        if isinstance(movement, dict):
            lines.append(f"Bank balance: ₪{movement.get('balance_after')}")
        settlement = outlook.get("expense_settlement")
        if isinstance(settlement, dict):
            lines.append(
                "Next card/reimbursement estimate: "
                f"-₪{settlement.get('credit_charges')} "
                f"+₪{settlement.get('expected_reimbursement')}"
            )
        projected = outlook.get("projected_balance_after_settlement")
        minimum = outlook.get("minimum_available_balance")
        available = outlook.get("available_above_minimum")
        if projected is not None and minimum is not None and available is not None:
            if outlook.get("on_track") is True:
                lines.append(
                    f"After next settlement: ₪{projected} — on track, "
                    f"₪{available} above the ₪{minimum} minimum."
                )
            else:
                shortfall = abs(Decimal(str(available)))
                lines.append(
                    f"After next settlement: ₪{projected} — ₪{shortfall} below "
                    f"the ₪{minimum} minimum."
                )
        transfer = outlook.get("recommended_savings_transfer")
        if transfer not in {None, "0.00"}:
            lines.append(
                f"Savings signal: move ₪{transfer} after the settlement clears."
            )
    else:
        lines.append("Bank outlook is unavailable because its data source is not configured.")

    budget = evaluation.get("budget")
    if isinstance(budget, dict):
        lines.append(
            f"Budget: ₪{budget.get('total_budget')} allocated; "
            f"₪{budget.get('actual_variable_spend')} variable spending."
        )
        overspends = budget.get("actual_overspends")
        projections = budget.get("material_projections")
        if isinstance(overspends, list) and overspends:
            lines.append(
                "Over budget: "
                + ", ".join(
                    f"{item['subcategory']} by ₪{item['amount']}"
                    for item in overspends
                    if isinstance(item, dict)
                )
                + "."
            )
        if isinstance(projections, list) and projections:
            lines.append(
                "Projected pressure: "
                + ", ".join(
                    f"{item['subcategory']} by ₪{item['amount']}"
                    for item in projections
                    if isinstance(item, dict)
                )
                + "."
            )
        if not overspends and not projections:
            lines.append("Budget evaluation: no material category exception detected.")
    return "\n".join(lines)
