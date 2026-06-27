from tools.notion_tools import (
    get_income_between_dates,
    get_finance_rules,
    get_database_schema,
    get_expenses_between_dates,
    get_last_expenses,
    get_movies_data_from_notion_database,
    get_spending_habits,
    get_financial_advisor_habits,
    update_financial_advisor_habit,
)
from tools.ideas_tools import create_idea_in_notion
from tools.israeli_market_tools import (
    get_exchange_rates,
    get_tase_stock_quote,
    get_tase_index,
)
from tools.monthly_budget.agent_tools import (
    delete_monthly_budget,
    review_monthly_budgets,
    update_monthly_budget,
)


def get_tools():
    """Base tools always available to the agent."""
    return [
        get_income_between_dates,
        get_finance_rules,
        get_database_schema,
        get_expenses_between_dates,
        get_last_expenses,
        get_movies_data_from_notion_database,
        get_spending_habits,
        get_financial_advisor_habits,
        update_financial_advisor_habit,
        review_monthly_budgets,
        update_monthly_budget,
        delete_monthly_budget,
        create_idea_in_notion,
        get_exchange_rates,
        get_tase_stock_quote,
        get_tase_index,
    ]


def get_workflow_tools(
    chat_id: str,
    budget_graph,
    budget_sessions: dict,
    pending_jobs: dict,
    budget_review_graph=None,
    budget_review_sessions: dict = None,
    uncategorized_review_graph=None,
):
    """Session-bound workflow trigger tools, created once per chat_id."""
    from tools.workflow_tools import (
        make_budget_review_tool,
        make_budget_tool,
        make_job_tool,
        make_uncategorized_review_tool,
    )

    tools = [
        make_budget_tool(chat_id, budget_graph, budget_sessions),
        make_job_tool(chat_id, pending_jobs),
    ]
    if budget_review_graph is not None and budget_review_sessions is not None:
        tools.append(make_budget_review_tool(chat_id, budget_review_graph, budget_review_sessions))
    if uncategorized_review_graph is not None:
        tools.append(make_uncategorized_review_tool(uncategorized_review_graph))
    return tools
