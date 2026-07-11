from __future__ import annotations

import os
from typing import Annotated, Any, Callable, Dict, List, TypedDict

from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages


Transaction = Dict[str, Any]
Suggestion = Dict[str, Any]
FetchTransactions = Callable[[], List[Transaction]]
SuggestTransactions = Callable[[List[Transaction]], List[Suggestion]]


class UncategorizedReviewState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    transactions: List[Transaction]
    suggestions: List[Suggestion]


def fetch_uncategorized_transactions() -> List[Transaction]:
    from personal_assistant.tools.notion_tools import _raw_notion_response_to_dict, notion_get_database_pages

    expenses_database_id = os.getenv("EXPENSES_DATABASE_ID")
    if not expenses_database_id:
        raise ValueError("Missing EXPENSES_DATABASE_ID environment variable.")

    raw = notion_get_database_pages.invoke(
        {
            "database_id": expenses_database_id,
            "filter": {
                "and": [
                    {"property": "Category", "multi_select": {"contains": "Uncategorized"}},
                    {"property": "Tag", "multi_select": {"contains": "Tal 👨🏻"}},
                ]
            },
            "sorts": [{"property": "Date", "direction": "descending"}],
        }
    )
    rows = _raw_notion_response_to_dict(
        ["Description", "Final", "Amount", "Category", "Sub Category", "Date"],
        raw,
    )
    for row in rows:
        row["Amount"] = row.get("Final") or row.get("Amount") or 0
        row.pop("Final", None)
    return rows


def suggest_uncategorized_categories(transactions: List[Transaction]) -> List[Suggestion]:
    """Sync uncategorized Notion expenses into the ML review queue.

    New expenses normally enter the queue when they're logged; this backfills
    anything older (or logged while the hook was broken) and returns the full
    pending queue so the respond node can present every open suggestion.
    """
    from personal_assistant.ml.expense_categorizer import review_queue
    from personal_assistant.ml.expense_categorizer.service import classify_and_enqueue

    for transaction in transactions:
        page_id = transaction.get("id") or ""
        if not page_id or review_queue.has_pending_for_page(page_id):
            continue
        classify_and_enqueue(
            notion_page_id=page_id,
            description=str(transaction.get("Description") or ""),
            amount=float(transaction.get("Amount") or 0),
            date=str(transaction.get("Date") or ""),
        )

    return [item.to_dict() for item in review_queue.pending_items()]


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
