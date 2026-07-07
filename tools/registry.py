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
    create_future_expense,
    create_future_purchase,
    create_future_vacation,
    get_current_budget,
    get_expense_summary,
    get_future_expenses,
    get_future_purchases,
    get_future_vacations,
    get_income_summary,
    get_latest_account_balances,
    get_transactions,
    set_actively_saving,
    update_financial_advisor_rule,
)
from tools.financial_advisor.memory import (
    get_current_bank_balance,
    get_financial_profile,
    get_financial_recommendations,
    log_financial_recommendation,
    update_bank_account_balance,
    update_emergency_fund_months,
    update_financial_recommendation_status,
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
        get_future_expenses,
        get_future_purchases,
        get_future_vacations,
        create_future_expense,
        create_future_purchase,
        create_future_vacation,
        set_actively_saving,
        get_financial_profile,
        get_current_bank_balance,
        update_bank_account_balance,
        update_emergency_fund_months,
        log_financial_recommendation,
        get_financial_recommendations,
        update_financial_recommendation_status,
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
