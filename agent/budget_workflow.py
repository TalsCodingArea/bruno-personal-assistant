"""
agent/budget_workflow.py — LangGraph-based monthly budget planning workflow.

Architecture:
  StateGraph with two nodes:
    1. "analyze"  — runs once at start: fetches Notion data, merges persisted prefs, emits first message
    2. "chat"     — handles every subsequent user message based on current phase

  The graph is compiled with interrupt_before=["chat"], so it pauses after
  emitting each bot message and waits for the next user input. State is
  persisted across Telegram messages via MemorySaver (keyed by thread_id = chat_id).

Phases (stored in state["phase"]):
  "budget_input"  → waiting for the user to enter their monthly budget
  "review"        → user adjusts recurring category amounts/membership
  "unexpected"    → user enters upcoming one-off expenses
  "carryover"     → user inputs savings carried over from last month
  "summary"       → bot shows full breakdown, user confirms
  "done"          → workflow complete

LLM usage (one call per user turn):
  _agent_turn() sends a context-rich system prompt per phase and returns:
    {"action": str, "data": {...}, "response": "<natural language reply>"}
  The "response" is shown to the user; "action" + "data" drive state changes.

Persistence:
  On "done" in the review phase, confirmed recurring categories and excluded names
  are saved to budget_data/repeating_categories.json via save_persisted_categories().
  On startup, analyze_node loads this file and merges it with fresh Notion data.

Integration with Telegram (use helpers, not graph.invoke directly):
  graph = create_budget_graph(llm)
  config = {"configurable": {"thread_id": str(chat_id)}}

  state = start_budget_workflow(graph, config)
  state = continue_budget_workflow(graph, config, user_text)   # sync
  state = await async_continue_budget_workflow(graph, config, user_text)  # async
"""

from __future__ import annotations

import json
import logging
import re
from functools import partial
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from tools.budget_tools import (
    analyze_spending_patterns,
    compute_budget_breakdown,
    compute_smart_projections,
    fetch_current_month_budget,
    find_category_by_name,
    find_savings_opportunities,
    format_analysis_message,
    format_breakdown_message,
    generate_budget_insights,
    identify_repeating_categories,
    load_persisted_categories,
    log_monthly_budget_to_notion,
    merge_categories_with_persisted,
    save_persisted_categories,
    update_budget_categories,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------

class BudgetState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    phase: str                      # see module docstring for valid values
    monthly_budget: float
    analysis: Dict[str, Any]
    repeating_categories: List[Dict]     # [{name, expected_amount, avg, months_present, trend}]
    suggested_new_categories: List[Dict]
    excluded_categories: List[str]       # category names permanently removed by the user
    unexpected_expenses: List[Dict]      # [{description, amount}]
    carryover: float
    breakdown: Optional[Dict]


def make_initial_state(preset_budget: float = 0.0) -> Dict[str, Any]:
    """
    Return a fresh BudgetState-compatible dict to start a new workflow run.

    Args:
        preset_budget: If > 0, skip the budget input phase and use this value directly.
    """
    return {
        "messages": [],
        "phase": "init",
        "monthly_budget": preset_budget,
        "analysis": {},
        "repeating_categories": [],
        "suggested_new_categories": [],
        "excluded_categories": [],
        "unexpected_expenses": [],
        "carryover": 0.0,
        "breakdown": None,
    }


# ---------------------------------------------------------------------------
# LLM agent turn  (one call per user message → action + natural-language reply)
# ---------------------------------------------------------------------------

def _call_llm_json(llm, system: str, user_text: str) -> Dict[str, Any]:
    """Send a structured prompt to the LLM and return decoded JSON."""
    resp = llm.invoke([
        SystemMessage(content=system),
        HumanMessage(content=user_text),
    ])
    raw = (resp.content or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON: %s", raw)
        return {}


def _agent_turn(state: BudgetState, user_text: str, llm) -> Dict[str, Any]:
    """
    Single LLM call per user turn.

    Builds a phase-specific prompt with full current state context, then asks
    the LLM to return:
      {"action": "<name>", "data": {...}, "response": "<message to show user>"}

    The "response" is the bot's natural-language reply.
    The "action" + "data" drive state changes in chat_node.
    """
    phase = state.get("phase")
    cats = state.get("repeating_categories", [])
    suggested = state.get("suggested_new_categories", [])
    budget = state.get("monthly_budget", 0.0)
    unexpected = state.get("unexpected_expenses", [])

    if phase == "budget_input":
        state_info = ""
        role = "The user needs to tell you their total monthly budget in Israeli Shekels (₪)."
        actions = """
  set_budget — user provided a valid amount
    {"action": "set_budget", "data": {"amount": <number>}, "response": "<your reply>"}
  clarify — amount unclear or missing
    {"action": "clarify", "data": {}, "response": "<your reply>"}"""

    elif phase == "review":
        cats_str = "\n".join(
            f"  • {c['name']}: ₪{c['expected_amount']:,.0f} {c.get('trend','→')}"
            for c in cats
        ) or "  (none yet)"
        sug_str = "\n".join(
            f"  • {c['name']}: ₪{c['avg']:,.0f}"
            for c in suggested
        ) or "  (none)"
        state_info = (
            f"Monthly budget: ₪{budget:,.0f}\n\n"
            f"Current recurring categories:\n{cats_str}\n\n"
            f"Suggested new categories (appeared recently):\n{sug_str}"
        )
        role = (
            "The user is reviewing their recurring monthly expense categories. "
            "They can freely adjust amounts, remove categories, add new ones, "
            "confirm suggested ones, or say they're done. "
            "If they remove a category, it will be permanently excluded from future suggestions."
        )
        actions = """
  adjust — change expected amount for an existing category
    {"action": "adjust", "data": {"category": "<name>", "amount": <number>}, "response": "<your reply>"}
  remove — permanently remove a category from recurring suggestions
    {"action": "remove", "data": {"category": "<name>"}, "response": "<your reply>"}
  add — add a brand new recurring category
    {"action": "add", "data": {"name": "<name>", "amount": <number>}, "response": "<your reply>"}
  confirm — accept a suggested category into the recurring list
    {"action": "confirm", "data": {"category": "<name>"}, "response": "<your reply>"}
  done — all categories look good, move on
    {"action": "done", "data": {}, "response": "<your reply>"}
  clarify — intent is unclear, ask a follow-up question
    {"action": "clarify", "data": {}, "response": "<your reply>"}"""

    elif phase == "unexpected":
        exp_str = "\n".join(
            f"  • {e['description']}: ₪{e['amount']:,.0f}" for e in unexpected
        ) or "  (none added yet)"
        state_info = (
            f"Monthly budget: ₪{budget:,.0f}\n\n"
            f"One-off expenses added so far:\n{exp_str}"
        )
        role = (
            "The user is listing upcoming one-off expenses for this month — "
            "non-recurring things like car service, tuition, fines, a specific purchase. "
            "Each message may add one expense or signal they are done."
        )
        actions = """
  add_expense — user named an expense with an amount
    {"action": "add_expense", "data": {"description": "<short label>", "amount": <positive number>}, "response": "<your reply>"}
  done — no more one-off expenses
    {"action": "done", "data": {}, "response": "<your reply>"}
  clarify — unclear input
    {"action": "clarify", "data": {}, "response": "<your reply>"}"""

    elif phase == "carryover":
        state_info = f"Monthly budget: ₪{budget:,.0f}"
        role = (
            "Ask if the user has savings from last month to add to this month's budget. "
            "A carryover of 0 is valid (they spent it all or it goes to a separate savings account)."
        )
        actions = """
  set_carryover — user gave a carryover amount (can be 0)
    {"action": "set_carryover", "data": {"amount": <number>}, "response": "<your reply>"}
  clarify — unclear
    {"action": "clarify", "data": {}, "response": "<your reply>"}"""

    elif phase == "summary":
        bd = state.get("breakdown") or {}
        state_info = (
            f"Monthly budget: ₪{bd.get('monthly_budget', budget):,.0f}\n"
            f"Carryover: ₪{bd.get('carryover', 0):,.0f}\n"
            f"Total available: ₪{bd.get('total_available', budget):,.0f}\n"
            f"Committed: ₪{bd.get('committed_total', 0):,.0f}\n"
            f"Discretionary: ₪{bd.get('discretionary', 0):,.0f}"
        )
        role = "The user is reviewing the final budget breakdown and deciding whether to confirm it."
        actions = """
  confirm — user approves the plan and wants to save it
    {"action": "confirm", "data": {}, "response": "<your reply>"}
  clarify — user wants to change something or is not sure
    {"action": "clarify", "data": {}, "response": "<your reply>"}"""

    else:
        return {"action": "clarify", "data": {}, "response": "I'm not sure what to do here. Please try again."}

    system = f"""You are a friendly personal finance assistant managing the user's monthly budget review.

=== Current State ===
{state_info}

=== Your role ===
{role}

=== Available actions ===
Return exactly ONE of the following as valid JSON (no markdown, no extra text):{actions}

Guidelines:
- Understand the user's message naturally — they don't need exact command syntax
- Be conversational and concise in the "response" field
- Currency is Israeli Shekels (₪ / ILS)
- If the user says something like "looks good", "all good", "move on", treat it as "done"
"""

    return _call_llm_json(llm, system, user_text)


# ---------------------------------------------------------------------------
# Graph node: analyze
# ---------------------------------------------------------------------------

def analyze_node(state: BudgetState, llm) -> Dict[str, Any]:
    """
    Fetch Notion expense data, merge with persisted category preferences,
    and emit the first bot message.  Runs exactly once at the start.
    """
    analysis = analyze_spending_patterns(lookback_months=3)
    detected_repeating, detected_suggested = identify_repeating_categories(analysis)

    # Load persisted preferences and merge with fresh Notion data
    persisted_confirmed, excluded_names = load_persisted_categories()
    repeating, suggested_new = merge_categories_with_persisted(
        detected_repeating, detected_suggested, persisted_confirmed, excluded_names
    )

    if not state.get("monthly_budget"):
        msg = (
            "💼 Budget Workflow\n\n"
            "What is your total budget for this month? (enter amount in ₪)"
        )
        return {
            "phase": "budget_input",
            "analysis": analysis,
            "repeating_categories": repeating,
            "suggested_new_categories": suggested_new,
            "excluded_categories": list(excluded_names),
            "messages": [AIMessage(content=msg)],
        }

    msg = format_analysis_message(analysis, repeating, suggested_new)
    return {
        "phase": "review",
        "analysis": analysis,
        "repeating_categories": repeating,
        "suggested_new_categories": suggested_new,
        "excluded_categories": list(excluded_names),
        "messages": [AIMessage(content=msg)],
    }


# ---------------------------------------------------------------------------
# Graph node: chat  (handles ALL user turns after the initial analysis)
# ---------------------------------------------------------------------------

def chat_node(state: BudgetState, llm) -> Dict[str, Any]:
    """
    Route each incoming user message through the LLM agent, then apply
    the returned action to update the workflow state.
    """
    last_human = next(
        (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        None,
    )
    if not last_human:
        return {}

    user_text = last_human.content.strip()
    phase = state.get("phase", "review")

    # ------------------------------------------------------------------ #
    # budget_input: user sends their monthly budget amount                #
    # ------------------------------------------------------------------ #
    if phase == "budget_input":
        parsed = _agent_turn(state, user_text, llm)
        action = parsed.get("action", "clarify")
        response = parsed.get("response", "")

        if action == "set_budget":
            budget = float(parsed.get("data", {}).get("amount", 0))
            if budget > 0:
                analysis_msg = format_analysis_message(
                    state["analysis"],
                    state["repeating_categories"],
                    state["suggested_new_categories"],
                )
                full_response = f"{response}\n\n{analysis_msg}" if response else analysis_msg
                return {
                    "monthly_budget": budget,
                    "phase": "review",
                    "messages": [AIMessage(content=full_response)],
                }

        return {"messages": [AIMessage(content=response or "Please enter a valid budget amount in ₪.")]}

    # ------------------------------------------------------------------ #
    # review: user adjusts the recurring category list                   #
    # ------------------------------------------------------------------ #
    if phase == "review":
        cats = list(state.get("repeating_categories", []))
        suggested = list(state.get("suggested_new_categories", []))
        excluded = list(state.get("excluded_categories", []))

        parsed = _agent_turn(state, user_text, llm)
        action = parsed.get("action", "clarify")
        data = parsed.get("data", {})
        response = parsed.get("response", "")

        if action == "done":
            total = sum(c.get("expected_amount", 0) for c in cats)
            # Persist confirmed categories and exclusions
            save_persisted_categories(cats, set(excluded))
            phase_msg = (
                f"Recurring categories confirmed — expected total ₪{total:,.0f}.\n\n"
                "Now, do you have any upcoming one-off expenses this month?\n"
                "(e.g. car service, tuition, a fine)\n\n"
                "Enter each one, or say 'done' / 'none' to skip."
            )
            full_response = f"{response}\n\n{phase_msg}" if response else phase_msg
            return {
                "phase": "unexpected",
                "repeating_categories": cats,
                "messages": [AIMessage(content=full_response)],
            }

        if action == "adjust":
            cat = find_category_by_name(data.get("category", ""), cats)
            amount = float(data.get("amount", 0))
            if cat and amount > 0:
                cat["expected_amount"] = round(amount)
                return {
                    "repeating_categories": cats,
                    "messages": [AIMessage(content=response or f"Updated {cat['name']} to ₪{amount:,.0f}.")],
                }

        if action == "remove":
            cat = find_category_by_name(data.get("category", ""), cats)
            if cat:
                cats.remove(cat)
                excluded.append(cat["name"])
                return {
                    "repeating_categories": cats,
                    "excluded_categories": excluded,
                    "messages": [AIMessage(content=response or f"Removed {cat['name']} and won't suggest it again.")],
                }

        if action == "add":
            name = data.get("name", "").strip()
            amount = float(data.get("amount", 0))
            if name and amount > 0:
                cats.append({
                    "name": name,
                    "expected_amount": round(amount),
                    "avg": amount,
                    "months_present": 0,
                    "trend": "→",
                })
                return {
                    "repeating_categories": cats,
                    "messages": [AIMessage(content=response or f"Added {name}: ₪{amount:,.0f}.")],
                }

        if action == "confirm":
            sug = find_category_by_name(data.get("category", ""), suggested)
            if sug:
                suggested.remove(sug)
                cats.append(sug)
                return {
                    "repeating_categories": cats,
                    "suggested_new_categories": suggested,
                    "messages": [AIMessage(content=response or f"Added {sug['name']}: ₪{sug['expected_amount']:,.0f}.")],
                }

        # clarify or unrecognised
        return {"messages": [AIMessage(content=response or (
            "Not sure I got that. You can adjust amounts, remove categories, add new ones, or say 'done'."
        ))]}

    # ------------------------------------------------------------------ #
    # unexpected: user lists one-off upcoming expenses                   #
    # ------------------------------------------------------------------ #
    if phase == "unexpected":
        parsed = _agent_turn(state, user_text, llm)
        action = parsed.get("action", "clarify")
        data = parsed.get("data", {})
        response = parsed.get("response", "")

        if action == "done":
            phase_msg = (
                "Do you have any savings from last month to carry over into this month's budget?\n"
                "(Enter ₪ amount, or 0 / 'none'.)"
            )
            full_response = f"{response}\n\n{phase_msg}" if response else phase_msg
            return {
                "phase": "carryover",
                "messages": [AIMessage(content=full_response)],
            }

        if action == "add_expense":
            description = data.get("description", "Expense").strip()
            amount = float(data.get("amount", 0))
            if amount > 0:
                expenses = list(state.get("unexpected_expenses", []))
                expenses.append({"description": description, "amount": round(amount)})
                return {
                    "unexpected_expenses": expenses,
                    "messages": [AIMessage(content=response or f"Added {description} — ₪{amount:,.0f}. Anything else?")],
                }

        return {"messages": [AIMessage(content=response or "Please enter an expense (e.g. '300 car service') or say 'done'.")]}

    # ------------------------------------------------------------------ #
    # carryover: savings from last month                                  #
    # ------------------------------------------------------------------ #
    if phase == "carryover":
        parsed = _agent_turn(state, user_text, llm)
        action = parsed.get("action", "clarify")
        data = parsed.get("data", {})
        response = parsed.get("response", "")

        if action in ("set_carryover", "clarify"):
            carryover = float(data.get("amount", 0)) if action == "set_carryover" else 0.0

            budget = state.get("monthly_budget", 0.0)
            repeating = state.get("repeating_categories", [])
            unexpected = state.get("unexpected_expenses", [])
            breakdown = compute_budget_breakdown(budget, repeating, unexpected, carryover)

            breakdown_msg = format_breakdown_message(breakdown)
            confirm_hint = "\nType 'confirm' to save this plan, or let me know if you'd like to change anything."
            full_response = f"{response}\n\n{breakdown_msg}{confirm_hint}" if response else f"{breakdown_msg}{confirm_hint}"

            return {
                "carryover": carryover,
                "phase": "summary",
                "breakdown": breakdown,
                "messages": [AIMessage(content=full_response)],
            }

        return {"messages": [AIMessage(content=response or "How much savings are you carrying over from last month? (0 if none)")]}

    # ------------------------------------------------------------------ #
    # summary: user confirms or asks for changes                         #
    # ------------------------------------------------------------------ #
    if phase == "summary":
        parsed = _agent_turn(state, user_text, llm)
        action = parsed.get("action", "clarify")
        response = parsed.get("response", "")

        if action == "confirm":
            budget = state.get("monthly_budget", 0.0)
            notion_url = ""
            try:
                notion_url = log_monthly_budget_to_notion(budget)
            except Exception as exc:
                logger.warning("Could not update Notion Budget DB: %s", exc)

            confirm_msg = response or "Budget plan confirmed! Your recurring categories have been saved for next month."
            if notion_url:
                confirm_msg += f"\n\nNotion page updated: {notion_url}"
            elif not notion_url:
                confirm_msg += "\n\n(Could not reach the Notion Budget DB — check BUDGET_DATABASE_ID in .env)"

            return {
                "phase": "done",
                "messages": [AIMessage(content=confirm_msg)],
            }

        return {"messages": [AIMessage(content=response or "Type 'confirm' to save the plan, or let me know what you'd like to adjust.")]}

    return {}


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def _phase_router(state: BudgetState) -> str:
    """After each chat turn, either loop or end."""
    return END if state.get("phase") == "done" else "chat"


def create_budget_graph(llm):
    """
    Build and compile the LangGraph budget workflow.

    Returns a compiled graph. Use the helpers below to drive it — do NOT
    call graph.invoke directly, as the resume pattern requires update_state first.
    """
    graph = StateGraph(BudgetState)

    graph.add_node("analyze", partial(analyze_node, llm=llm))
    graph.add_node("chat", partial(chat_node, llm=llm))

    graph.set_entry_point("analyze")
    graph.add_edge("analyze", "chat")
    graph.add_conditional_edges("chat", _phase_router)

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer, interrupt_before=["chat"])


# ---------------------------------------------------------------------------
# Invocation helpers  (always use these — do NOT call graph.invoke directly)
# ---------------------------------------------------------------------------

async def async_start_budget_workflow(graph, config: dict, preset_budget: float = 0.0) -> dict:
    """Async variant of start_budget_workflow for use inside Telegram handlers."""
    return await graph.ainvoke(make_initial_state(preset_budget=preset_budget), config)


def start_budget_workflow(graph, config: dict, preset_budget: float = 0.0) -> dict:
    """
    Start a fresh budget workflow run.

    Args:
        graph:         Compiled graph from create_budget_graph().
        config:        {"configurable": {"thread_id": <unique session id>}}.
        preset_budget: If > 0, skip the budget-input phase.

    Returns the current graph state; check state["messages"] for the bot reply.
    """
    return graph.invoke(make_initial_state(preset_budget=preset_budget), config)


def continue_budget_workflow(graph, config: dict, user_text: str) -> dict:
    """
    Feed the user's next message into the workflow and advance to the next step.

    Uses update_state + invoke(None) to correctly resume from an interrupt_before
    checkpoint (rather than restarting the graph from the entry point).
    """
    graph.update_state(config, {"messages": [HumanMessage(content=user_text)]})
    return graph.invoke(None, config)


async def async_continue_budget_workflow(graph, config: dict, user_text: str) -> dict:
    """Async variant of continue_budget_workflow for use inside Telegram handlers."""
    await graph.aupdate_state(config, {"messages": [HumanMessage(content=user_text)]})
    return await graph.ainvoke(None, config)


# ===========================================================================
# Budget Review Workflow
# ===========================================================================
# A separate LangGraph that:
#   1. Fetches the current budget from Notion (by category)
#   2. Fetches current month expenses so far
#   3. Fetches current month income
#   4. Compares actuals vs budget per category — surfaces deviations >20%
#   5. Proposes adjustments and asks user to approve/reject each one
#   6. On approval, updates the Notion budget page
# ===========================================================================

class BudgetReviewState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    phase: str
    budget_page_id: str
    budget_url: str
    month: str
    budget_by_category: Dict[str, float]
    actual_by_category: Dict[str, float]
    actual_by_subcategory: Dict[str, float]
    projections: Dict[str, Any]             # output of compute_smart_projections
    insights: List[str]
    savings_opportunities: List[Dict]       # output of find_savings_opportunities (when over budget)
    income_total: float
    proposed_changes: Dict[str, float]
    approved_changes: Dict[str, float]


def _build_review_message(
    month: str,
    budget: Dict[str, float],
    projections: Dict[str, Any],
    insights: List[str],
    savings_opportunities: List[Dict],
    income: float,
    proposed: Dict[str, float],
    days_elapsed: int,
    days_in_month: int,
) -> str:
    """Build the review message with projections, insights, and savings opportunities."""
    lines = [f"📊 *Budget Review — {month}* (day {days_elapsed}/{days_in_month})\n"]

    total_budget = sum(budget.values())
    total_projected = sum(d["projected"] for d in projections.values())
    if income > 0:
        lines.append(
            f"💰 Income: ₪{income:,.0f}  |  Budgeted: ₪{total_budget:,.0f}  |  "
            f"Projected spend: ₪{total_projected:,.0f}  |  Projected savings: ₪{income - total_projected:,.0f}\n"
        )

    # Per-category: budget / actual so far / projected end-of-month
    lines.append("*Category breakdown:*")
    for cat in sorted(projections, key=lambda c: -projections[c]["budget"]):
        d = projections[cat]
        if d["budget"] == 0 and d["actual"] == 0:
            continue
        flag = " 🔴" if d["over_budget"] and d["pct_over"] > 20 else (" 🟡" if d["over_budget"] else "")
        impulse_note = " _(impulse capped)_" if d["is_impulse"] else ""
        fixed_note = " _(fixed)_" if d["is_fixed"] else ""
        lines.append(
            f"  • {cat}: ₪{d['budget']:,.0f} budget  "
            f"₪{d['actual']:,.0f} so far  →  *₪{d['projected']:,.0f} projected*"
            f"{flag}{impulse_note}{fixed_note}"
        )

    # Insights
    if insights:
        lines.append("")
        for insight in insights:
            lines.append(insight)

    # Subcategory savings opportunities (only when over budget)
    if savings_opportunities:
        lines.append("\n*Where you could save:*")
        for opp in savings_opportunities:
            saving = opp["suggested_saving"]
            lines.append(
                f"  • {opp['subcategory']}: ₪{opp['actual']:,.0f} this month "
                f"(avg ₪{opp['historical_avg']:,.0f}) — could save ~₪{saving:,.0f}"
            )

    # Proposed budget adjustments
    if proposed:
        lines.append("\n*Proposed budget adjustments:*")
        for cat, new_val in proposed.items():
            old_val = budget.get(cat, 0.0)
            lines.append(f"  • {cat}: ₪{old_val:,.0f} → ₪{new_val:,.0f}")
        new_total = total_budget + sum(proposed[c] - budget.get(c, 0) for c in proposed)
        if income > 0:
            lines.append(f"  With changes: save ₪{income - new_total:,.0f} instead of ₪{income - total_budget:,.0f}")
        lines.append("\nApprove changes? Reply 'all', 'none', or name specific categories.")
    else:
        lines.append("\nReply 'done' to close.")

    return "\n".join(lines)


def _propose_adjustments(
    projections: Dict[str, Any],
    threshold_pct: float = 15.0,
) -> Dict[str, float]:
    """
    Propose new budget values for categories projected to exceed budget by > threshold_pct.
    Uses the projection (not raw actual) as the basis — smarter about impulse vs trend.
    Rounds proposed value up to nearest 50.
    """
    proposed: Dict[str, float] = {}
    for cat, d in projections.items():
        if d["over_budget"] and d["pct_over"] > threshold_pct and d["budget"] > 0:
            proposed[cat] = float(int((d["projected"] * 1.05 + 49) // 50) * 50)
    return proposed


def _review_analyze_node(state: BudgetReviewState, llm) -> Dict[str, Any]:
    """Fetch budget, expenses, and income; run smart projections, insights, and savings analysis."""
    import calendar
    from datetime import date
    from tools.notion_tools import get_expenses_between_dates, get_income_between_dates
    from tools.notion_tools import get_spending_habits
    from tools.budget_tools import load_persisted_categories

    today = date.today()
    start = today.replace(day=1).isoformat()
    end = today.isoformat()
    days_elapsed = today.day
    days_in_month = calendar.monthrange(today.year, today.month)[1]

    # Fetch budget from Notion
    try:
        budget_data = fetch_current_month_budget()
    except (ValueError, RuntimeError) as exc:
        return {
            "phase": "done",
            "messages": [AIMessage(content=f"❌ Could not load budget: {exc}")],
        }

    # Fetch actual expenses
    try:
        expense_result = get_expenses_between_dates.invoke({"start_date": start, "end_date": end})
        actual_by_category = expense_result.get("by_category", {})
        actual_by_subcategory = expense_result.get("by_subcategory", {})
    except Exception as exc:
        actual_by_category = {}
        actual_by_subcategory = {}
        logger.warning("Could not fetch expenses for review: %s", exc)

    # Fetch income
    try:
        income_rows = get_income_between_dates.invoke({"start_date": start, "end_date": end})
        income_total = sum(r.get("Amount") or 0 for r in income_rows if isinstance(r.get("Amount"), (int, float)))
    except Exception as exc:
        income_total = 0.0
        logger.warning("Could not fetch income for review: %s", exc)

    # Load spending habits and repeating categories for smart projections
    try:
        habits = get_spending_habits.invoke({})
        habits_by_category = habits.get("by_category", {})
        habits_by_subcategory = habits.get("by_subcategory", {})
    except Exception:
        habits_by_category = {}
        habits_by_subcategory = {}

    repeating_confirmed, _ = load_persisted_categories()

    budget_by_category = budget_data["categories"]
    total_budget = sum(budget_by_category.values())

    # Smart projections
    projections = compute_smart_projections(
        budget_by_category, actual_by_category,
        habits_by_category, repeating_confirmed,
        days_elapsed, days_in_month,
        actual_by_subcategory=actual_by_subcategory,
        habits_by_subcategory=habits_by_subcategory,
    )

    # Insights
    insights = generate_budget_insights(projections, income_total, total_budget)

    # Sub-category savings opportunities — only when meaningfully over budget
    total_projected = sum(d["projected"] for d in projections.values())
    savings_opportunities: List[Dict] = []
    if total_projected > total_budget * 1.05:
        savings_opportunities = find_savings_opportunities(
            projections, actual_by_subcategory, habits_by_subcategory
        )

    proposed = _propose_adjustments(projections)

    msg = _build_review_message(
        budget_data["month"], budget_by_category, projections,
        insights, savings_opportunities, income_total,
        proposed, days_elapsed, days_in_month,
    )

    return {
        "phase": "confirm" if proposed else "done",
        "budget_page_id": budget_data["page_id"],
        "budget_url": budget_data["url"],
        "month": budget_data["month"],
        "budget_by_category": budget_by_category,
        "actual_by_category": actual_by_category,
        "actual_by_subcategory": actual_by_subcategory,
        "projections": projections,
        "insights": insights,
        "savings_opportunities": savings_opportunities,
        "income_total": income_total,
        "proposed_changes": proposed,
        "approved_changes": {},
        "messages": [AIMessage(content=msg)],
    }


def _review_chat_node(state: BudgetReviewState, llm) -> Dict[str, Any]:
    """Handle user approval/rejection of proposed changes."""
    last_human = next(
        (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        None,
    )
    if not last_human:
        return {}

    user_text = last_human.content.strip().lower()
    proposed = state.get("proposed_changes", {})
    budget = state.get("budget_by_category", {})
    income = state.get("income_total", 0.0)

    # Parse approval
    if "none" in user_text or "no" == user_text:
        return {
            "phase": "done",
            "messages": [AIMessage(content="No changes made. Budget stays as is 👍")],
        }

    if "all" in user_text:
        approved = dict(proposed)
    else:
        # Match category names mentioned by the user
        approved = {
            cat: val for cat, val in proposed.items()
            if any(word in user_text for word in cat.lower().replace("🛒","").replace("💰","").split())
        }

    if not approved:
        return {
            "messages": [AIMessage(
                content="Didn't catch which categories to approve. Say 'all', 'none', or name the categories."
            )],
        }

    # Apply to Notion
    try:
        update_budget_categories(state["budget_page_id"], approved)
    except Exception as exc:
        return {
            "phase": "done",
            "messages": [AIMessage(content=f"❌ Failed to update Notion: {exc}")],
        }

    new_budget = {**budget, **approved}
    new_total = sum(new_budget.values())
    new_savings = income - new_total

    lines = ["✅ Budget updated in Notion!\n", "*Changes applied:*"]
    for cat, val in approved.items():
        lines.append(f"  • {cat}: ₪{budget.get(cat, 0):,.0f} → ₪{val:,.0f}")
    lines.append(f"\nNew totals — budgeted: ₪{new_total:,.0f}  |  to save: ₪{new_savings:,.0f}")
    if state.get("budget_url"):
        lines.append(f"\nNotion page: {state['budget_url']}")

    return {
        "phase": "done",
        "approved_changes": approved,
        "messages": [AIMessage(content="\n".join(lines))],
    }


def _review_phase_router(state: BudgetReviewState) -> str:
    return END if state.get("phase") == "done" else "chat"


def create_budget_review_graph(llm):
    """Build and compile the budget review workflow graph."""
    graph = StateGraph(BudgetReviewState)
    graph.add_node("analyze", partial(_review_analyze_node, llm=llm))
    graph.add_node("chat", partial(_review_chat_node, llm=llm))
    graph.set_entry_point("analyze")
    graph.add_edge("analyze", "chat")
    graph.add_conditional_edges("chat", _review_phase_router)
    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer, interrupt_before=["chat"])


async def async_start_budget_review(graph, config: dict) -> dict:
    initial: Dict[str, Any] = {
        "messages": [], "phase": "init",
        "budget_page_id": "", "budget_url": "", "month": "",
        "budget_by_category": {}, "actual_by_category": {}, "actual_by_subcategory": {},
        "projections": {}, "insights": [], "savings_opportunities": [],
        "income_total": 0.0, "proposed_changes": {}, "approved_changes": {},
    }
    return await graph.ainvoke(initial, config)


async def async_continue_budget_review(graph, config: dict, user_text: str) -> dict:
    await graph.aupdate_state(config, {"messages": [HumanMessage(content=user_text)]})
    return await graph.ainvoke(None, config)
