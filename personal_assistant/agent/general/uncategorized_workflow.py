"""Human-in-the-loop review workflow for uncategorized expenses.

This graph is the interactive part: present every queued ML suggestion so the
user can confirm/correct each one. The underlying data pull (Notion fetch +
review-queue sync) lives in tools/expense_review_tools.py and is also exposed
directly as the get_uncategorized_expenses_status tool for quick status
questions that don't need the full review.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable, Dict, List, TypedDict

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages

from personal_assistant.tools.expense_review_tools import (
    fetch_uncategorized_expenses,
    sync_uncategorized_to_review_queue,
)

Transaction = Dict[str, Any]
Suggestion = Dict[str, Any]
FetchTransactions = Callable[[], List[Transaction]]
SuggestTransactions = Callable[[List[Transaction]], List[Suggestion]]

# Backwards-compatible aliases (the fetch/sync logic moved to the tools layer).
fetch_uncategorized_transactions = fetch_uncategorized_expenses
suggest_uncategorized_categories = sync_uncategorized_to_review_queue


class UncategorizedReviewState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    transactions: List[Transaction]
    suggestions: List[Suggestion]


def _format_amount(value: Any) -> str:
    if isinstance(value, bool):
        return "0"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    if isinstance(value, str):
        try:
            return f"{float(value):g}"
        except ValueError:
            return value
    return "0"


def format_uncategorized_review(transactions: List[Transaction], suggestions: List[Suggestion]) -> str:
    if not transactions and not suggestions:
        return "No uncategorized Tal expenses found. Suspiciously tidy. I approve."

    lines = [f"{len(suggestions)} expense(s) waiting for category review:", ""]
    for index, item in enumerate(suggestions, start=1):
        review_id = item.get("review_id") or "?"
        description = item.get("description") or item.get("Description") or "Expense"
        amount = item.get("amount") or item.get("Amount") or 0
        date = str(item.get("date") or item.get("Date") or "No date")[:10]
        sub_category = item.get("predicted_sub_category")
        if sub_category:
            confidence = item.get("confidence")
            confidence_text = f"{confidence:.0%}" if isinstance(confidence, (int, float)) else "?"
            suggestion_text = f"{item.get('predicted_category')} / {sub_category} ({confidence_text})"
        else:
            suggestion_text = "no prediction (model not trained yet)"
        lines.append(
            f"{index}. [{review_id}] {date} | {description} | ₪{_format_amount(amount)} -> {suggestion_text}"
        )
    lines += [
        "",
        "Confirm or correct each item; resolutions update Notion and retrain the model.",
    ]
    return "\n".join(lines)


def create_uncategorized_review_graph(
    fetcher: FetchTransactions = fetch_uncategorized_transactions,
    suggester: SuggestTransactions = suggest_uncategorized_categories,
):
    async def fetch_node(state: UncategorizedReviewState) -> dict[str, List[Transaction]]:
        return {"transactions": fetcher()}

    async def suggest_node(state: UncategorizedReviewState) -> dict[str, List[Suggestion]]:
        return {"suggestions": suggester(state.get("transactions", []))}

    async def respond_node(state: UncategorizedReviewState) -> dict[str, List[BaseMessage]]:
        return {
            "messages": [
                AIMessage(
                    content=format_uncategorized_review(
                        state.get("transactions", []),
                        state.get("suggestions", []),
                    )
                )
            ]
        }

    graph = StateGraph(UncategorizedReviewState)
    graph.add_node("fetch", fetch_node)
    graph.add_node("suggest", suggest_node)
    graph.add_node("respond", respond_node)
    graph.set_entry_point("fetch")
    graph.add_edge("fetch", "suggest")
    graph.add_edge("suggest", "respond")
    graph.set_finish_point("respond")
    return graph.compile()


async def async_start_uncategorized_review(graph) -> UncategorizedReviewState:
    return await graph.ainvoke({"messages": [], "transactions": [], "suggestions": []})
