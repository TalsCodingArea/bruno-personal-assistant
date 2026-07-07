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
    """Stub categorizer. Later this can use prior categorized expenses."""
    return transactions


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
    if not transactions:
        return "No uncategorized Tal expenses found. Suspiciously tidy. I approve."

    lines = [
        f"Found {len(transactions)} uncategorized Tal expense(s).",
        "",
        "Suggestions are stubbed for now, so I’m returning the fetched transactions unchanged:",
    ]
    for index, item in enumerate(suggestions, start=1):
        description = item.get("Description") or item.get("description") or "Expense"
        amount = item.get("Amount") or item.get("amount") or 0
        date = item.get("Date") or item.get("date") or "No date"
        url = item.get("url")
        line = f"{index}. {date} | {description} | ₪{_format_amount(amount)}"
        if url:
            line += f" | {url}"
        lines.append(line)
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
