from tools.notion_tools import (
    get_income_between_dates,
    get_finance_rules,
    get_database_schema,
    get_expenses_between_dates,
    get_last_expenses,
    get_spending_habits,
    get_financial_advisor_habits,
    update_financial_advisor_habit,
)
from tools.monthly_budget.agent_tools import (
    apply_monthly_budget_plan,
    delete_monthly_budget,
    preview_monthly_budget_plan,
    review_monthly_budget_status,
    review_monthly_budgets,
    set_monthly_budget,
    update_monthly_budget,
)
from tools.financial_advisor.notion_tools import (
    create_balance_snapshot,
    create_financial_desire,
    create_future_purchase,
    create_future_vacation,
    create_future_obligation,
    get_current_budget,
    get_expense_summary,
    get_financial_desires,
    get_future_purchases,
    get_future_vacations,
    get_future_obligations,
    get_income_summary,
    get_latest_account_balances,
    get_transactions,
    log_financial_recommendation,
    update_financial_advisor_rule,
    update_financial_desire_status,
    update_future_obligation,
)
from tools.financial_advisor.memory import (
    get_current_bank_balance,
    get_financial_profile,
    update_bank_account_balance,
    update_emergency_fund_months,
)


def get_tools():
    """Base tools always available to the agent."""
    return [
        get_income_between_dates,
        get_finance_rules,
        get_database_schema,
        get_expenses_between_dates,
        get_last_expenses,
        get_spending_habits,
        get_financial_advisor_habits,
        update_financial_advisor_habit,
        review_monthly_budgets,
        preview_monthly_budget_plan,
        apply_monthly_budget_plan,
        review_monthly_budget_status,
        set_monthly_budget,
        update_monthly_budget,
        delete_monthly_budget,
        get_expense_summary,
        get_transactions,
        get_income_summary,
        get_latest_account_balances,
        get_current_budget,
        get_future_obligations,
        get_financial_desires,
        get_future_purchases,
        get_future_vacations,
        create_financial_desire,
        create_future_purchase,
        create_future_vacation,
        update_financial_desire_status,
        create_future_obligation,
        update_future_obligation,
        create_balance_snapshot,
        get_financial_profile,
        get_current_bank_balance,
        update_bank_account_balance,
        update_emergency_fund_months,
        log_financial_recommendation,
        update_financial_advisor_rule,
    ]


def get_workflow_tools(
    chat_id: str,
    pending_jobs: dict,
    uncategorized_review_graph=None,
):
    """Session-bound workflow trigger tools, created once per chat_id."""
    from tools.workflow_tools import (
        make_job_tool,
        make_uncategorized_review_tool,
    )

    tools = [
        make_job_tool(chat_id, pending_jobs),
    ]
    if uncategorized_review_graph is not None:
        tools.append(make_uncategorized_review_tool(uncategorized_review_graph))
    return tools
