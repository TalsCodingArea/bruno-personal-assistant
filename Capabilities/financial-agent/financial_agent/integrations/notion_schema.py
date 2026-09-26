"""Notion data-source and property-name mappings."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FinanceDataSources:
    """Runtime IDs supplied by environment-backed settings."""

    expenses: str
    income: str
    budgets: str
    future_expenses: str
    financial_rules: str


@dataclass(frozen=True, slots=True)
class FinanceProperties:
    expense_description: str = "Description"
    expense_category: str = "Category"
    expense_subcategory: str = "Sub Category"
    expense_amount: str = "Amount"
    expense_date: str = "Date"
    expense_payment_type: str = "Payment Method"
    expense_type: str = "Type"
    expense_tag: str = "Tag"
    expense_actual: str = "Actual"
    expense_final: str = "Final"
    expense_invoice: str = "Invoice"
    owner_tag: str = "Tal 👨🏻"

    income_name: str = "Name"
    income_amount: str = "Amount"
    income_date: str = "Date"

    budget_name: str = "Name"
    budget_date: str = "Date"
    budget_amount: str = "Budget"
    budget_progressive: str = "Progressive"
    budget_volatility: str = "Volatility"
    budget_baseline: str = "Baseline Budget"
    budget_last_adjustment_id: str = "Last Adjustment ID"
    budget_last_adjustment_reason: str = "Last Adjustment Reason"
    budget_last_adjustment_at: str = "Last Adjustment At"


@dataclass(frozen=True, slots=True)
class PlannedExpenseProperties:
    """Unset until the Future Expenses schema is supplied."""

    name: str | None = None
    target_amount: str | None = None
    due_date: str | None = None
    saved_amount: str | None = None


@dataclass(frozen=True, slots=True)
class FinancialProfileProperties:
    """Case-sensitive property contract for the Financial Rules data source."""

    name: str = "Name"
    key: str = "Key"
    kind: str = "Kind"
    scope: str = "Scope"
    statement: str = "Statement"
    status: str = "Status"
    supersedes: str = "Supersedes"
    operation_id: str = "Operation ID"
