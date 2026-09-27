"""LangGraph Agent Server export used by LangSmith Studio."""

from financial_agent.bootstrap import build_finance_application
from financial_agent.config import get_settings
from financial_agent.graphs.conversation import build_conversation_graph
from financial_agent.integrations.openai_model import build_openai_chat_model
from financial_agent.observability import configure_langsmith_environment

settings = get_settings()
configure_langsmith_environment(settings)

# Module lifetime matches the local Agent Server process, so the shared async Notion client
# and its connection pool are reused across Studio runs.
model = build_openai_chat_model(settings)
application = build_finance_application(settings, mutation_review_model=model)

# Agent Server injects and owns checkpoint persistence. Do not attach an application saver here.
graph = build_conversation_graph(
    model,
    application.tools.approval_conversation,
    currency=settings.currency,
    profile_service=application.profile,
)

# Separate, standalone workflow. Invoke with {"as_of": "YYYY-MM-DD"}; it has no
# conversation thread and relies on Notion operation IDs for retry safety.
daily_budget_graph = application.daily_budget_graph

# Event-driven workflow. Invoke with event_id, notion_page_id, observed_at, and event_type.
# It keeps idempotency and alert state in its own operational SQLite ledger.
expense_monitor_graph = application.expense_monitor_graph
