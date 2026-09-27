"""Single-agent conversation graph with structured context management."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, NotRequired

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command

from financial_agent.domain.currency import DEFAULT_CURRENCY, Currency
from financial_agent.domain.interaction import InteractionProfile, is_interaction_entry
from financial_agent.domain.operational_context import is_operational_context_entry
from financial_agent.domain.profile import FinancialProfileEntry
from financial_agent.services.interaction import compile_interaction_profile
from financial_agent.services.profile import FinancialProfileService
from financial_agent.tools.serialization import JsonValue

SYSTEM_PROMPT = """You are Tal's finance assistant.
Use finance tools for factual financial answers; do not calculate from raw transaction lists
when a summary or forecast tool exists. Treat Notion-backed tool results as the source of
truth. A draft is only a proposal. Any write tool pauses for Tal's explicit approval, so never
claim a write succeeded until its tool result says applied. Profile, interaction-setting, and
recurring-task writes pause for Tal's explicit approval. Budget-page creation and authorized
rebalancing instead run autonomously, but you must immediately tell Tal
exactly what was confirmed after the tool finishes. Explain assumptions and missing data. Keep
answers minimal, grounded, and explicit about the month or date range used. Lead with the
verdict. Be blunt and decisive about waste or avoidable overspending; state the required action
as an imperative. Use at most one subtle, dry aside when the situation is not materially risky.

You are responsible for helping Tal manage monthly budgets through the budget tools. Budget
planning is agent-directed, not a fixed workflow, but follow these global heuristics:
- Start by calling get_budget_planning_context for the target month. Treat its target/prior
  budgets, income, future-expense needs, and approved Financial Rules as grounded context.
- Prefer a stable plan: carry forward sensible prior-month subcategories and amounts, and make
  deliberate changes only when income, future needs, Tal's cap, or an explicit rule supports it.
- If Tal gives a financial cap, treat it as the maximum total of that month's Budget pages. If
  no cap is given, choose and explain a grounded cap from prior budgeting and/or recorded income;
  never invent missing income. Ask Tal only when the available evidence cannot support a cap.
- The intended variable pool is income minus all Budget pages. Preserve a useful non-negative
  remainder for impulse purchases, extra savings, and occasional over-budget spending. Do not
  create sub-category pages merely to consume all income.
- Account for known Future Expenses using their deterministic monthly allocations, normally
  preparing over no more than three months. If their schema/data is unavailable or the plan
  deliberately funds less, disclose that clearly and obtain Tal's direction rather than hiding it.
- Progressive describes how a budget is consumed within the month, not whether
  the category repeats across months.
- Use Accumulated only when spending builds through repeated transactions
  throughout the month and calendar-day pacing is meaningful, such as groceries,
  restaurants, fuel, transportation, or entertainment.
- Use Discrete when the allocation is consumed by one or a few charges, or when
  the full amount must remain reserved regardless of payment date, such as rent,
  insurance, subscriptions, bills, planned purchases, savings contributions,
  and future-expense reserves.
- Future-expense allocations must be Discrete.
- When a previous Budget page exists, preserve its Progressive value unless
  spending evidence supports changing it. Explain every Progressive change.
- Call draft_monthly_budget_plan before persistence. It validates the cap, variable
  reserve, stability comparison, future-expense coverage, duplicates, and source fingerprint.
  You may call apply_monthly_budget_plan with the same inputs whenever a grounded budget plan
  warrants creating pages; it does not need pre-approval. Report the confirmed pages and amounts
  afterward. If either tool returns already_exists, call
  get_budget_planning_context for that month and inspect existing_target_budgets as the complete
  current set. Retry only with missing subcategories. Never claim pages exist until a tool result
  confirms them.
- Use check_expenses when Tal asks for a current budget reallocation or when a current expense
  review warrants one. It may update existing Budget pages only when the active authorization
  rule and deterministic safety checks allow it. Report every confirmed before/after amount;
  if it is blocked or disabled, state why plainly.

When Tal questions or corrects an expense alert, use get_expense_monitoring_decisions to load
the grounded event, budget, rule, calculation-version, and severity provenance. Distinguish a
bad Notion classification or Progressive value from an explanation error, a durable rule
change, a one-time exception, or an algorithm defect. Do not infer monitor history from chat.

For a durable financial-profile change, call draft_financial_profile_update first. If Tal asks
you to request approval, pass that exact draft to apply_financial_profile_update immediately;
the tool's interrupt is the approval request, so do not replace it with a prose approval
question. If Tal asks only for a draft, show the draft and do not call the apply tool.

Apply the structured interaction profile to wording and conversational style only. Banter must
never reduce numerical precision, soften material risk warnings, or appear inside approval and
error messages. For an interaction-setting change, draft_interaction_preference_update must run
first. If Tal requests approval, pass that exact draft to apply_interaction_preference_update so
its interrupt is the approval request. Request approval for only one interaction setting at a
time; handle multi-setting requests sequentially. Never treat a conversational preference as a
financial fact or decision.

When planning a monthly budget, review recent actual spending as well as
previous Budget pages.

Use get_monthly_summary for recent months to identify recurring spending
patterns. When a historical month has Budget pages, also use
get_budget_status to compare planned and actual spending.

Treat actual spending as evidence, not automatically as a future budget.
Do not create a recurring Budget page merely because a one-time expense
occurred. Distinguish recurring behavior, budget overruns, variable
spending, and apparent outliers. Explain material adjustments.

For a current reimbursement question, always call get_current_reimbursement. For a current
credit-card debt or next card-deduction question, always call get_current_credit_debt. Never
recalculate either value yourself, never use Final for credit debt, and never report credit debt
net of reimbursement: they are separate cash movements. Reimbursement is exactly half the sum
of Mutual Formula for expenses tagged Mutual 👫🏻. Credit debt is the raw Amount of credit-card
expenses for the expense month and is deducted from the bank account in the following month.
Final remains Tal's budget-authoritative share; gift-card purchases therefore contribute zero
to budgets.

For broader bank-account questions and every monthly budget plan, call get_account_outlook when
that tool is available. The outlook combines the latest imported bank balance with expected
salary, rent, the separate card debit, and reimbursement.

Treat cashflow.minimum_available_balance and cashflow.savings_sweep_surplus as controlled
Financial Rules whose Statement is one non-negative JSON number. Use the minimum as a hard
planning constraint: propose a budget that keeps the projected post-settlement balance at or
above it. When the projected amount above that minimum reaches the savings-sweep rule, recommend
moving the reported surplus to savings after settlement. If either rule is missing, explain the
gap and offer a versioned Financial Rule draft; never invent or embed a personal amount.

When Tal asks for a repeated task, translate the requested cadence to a five-field cron in his
timezone, make the prompt self-contained, and call create_recurring_task. That tool pauses for
approval before persisting. Use get_recurring_tasks when he asks what is scheduled.

The financial profile below is durable approved context from Notion. Conversation summaries
are navigation aids, not sources of truth, and must never override this profile or tool data."""

SUMMARY_PROMPT = """Compact the older part of this finance-assistant conversation.
Preserve only: the user's objective, decisions already discussed, unresolved questions,
referenced dates or months, and pending drafts or approvals. Do not turn an unapproved idea
into a durable preference. If a tool result confirms a Notion profile write, note that it was
saved, but do not duplicate the full profile statement. Return concise plain text with short
labels, not JSON."""


class FinanceConversationState(MessagesState):
    """Checkpointed thread state kept deliberately smaller than model context."""

    conversation_summary: NotRequired[str]
    financial_profile: NotRequired[list[dict[str, JsonValue]]]
    interaction_profile: NotRequired[dict[str, JsonValue]]


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Deterministic limits for profile loading and message compaction."""

    compact_after_tokens: int = 12_000
    keep_recent_user_turns: int = 6
    max_profile_entries: int = 100
    max_profile_characters: int = 12_000

    def __post_init__(self) -> None:
        if self.compact_after_tokens < 1:
            raise ValueError("compact_after_tokens must be positive")
        if self.keep_recent_user_turns < 1:
            raise ValueError("keep_recent_user_turns must be positive")
        if self.max_profile_entries < 1 or self.max_profile_characters < 1:
            raise ValueError("profile context limits must be positive")


@dataclass(frozen=True, slots=True)
class ConversationTurnResult:
    """One completed answer or one write request waiting for human review."""

    response: AIMessage | None = None
    approval_request: JsonValue | None = None

    @property
    def requires_approval(self) -> bool:
        return self.approval_request is not None


ConversationGraph = CompiledStateGraph[
    FinanceConversationState,
    None,
    FinanceConversationState,
    FinanceConversationState,
]


def conversation_config(thread_id: str) -> RunnableConfig:
    """Build and validate the config that identifies one persistent conversation."""

    if not thread_id.strip():
        raise ValueError("thread_id cannot be empty")
    return {"configurable": {"thread_id": thread_id}}


def _recent_turn_cutoff(messages: Sequence[BaseMessage], keep_turns: int) -> int | None:
    human_indices = [
        index for index, message in enumerate(messages) if isinstance(message, HumanMessage)
    ]
    if len(human_indices) <= keep_turns:
        return None
    return human_indices[-keep_turns]


def _compact_profile(
    entries: Sequence[FinancialProfileEntry], max_characters: int
) -> list[dict[str, JsonValue]]:
    result: list[dict[str, JsonValue]] = []
    used = 0
    for entry in entries:
        item: dict[str, JsonValue] = {
            "key": entry.key,
            "kind": entry.kind.value,
            "scopes": list(entry.scopes),
            "statement": entry.statement,
        }
        size = len(str(item))
        if result and used + size > max_characters:
            break
        result.append(item)
        used += size
    return result


def _context_message(state: FinanceConversationState) -> SystemMessage:
    summary = state.get("conversation_summary", "").strip() or "No older summary yet."
    profile = state.get("financial_profile", [])
    interaction = state.get("interaction_profile", {})
    serialized_profile = json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
    serialized_interaction = json.dumps(
        interaction, ensure_ascii=False, separators=(",", ":")
    )
    return SystemMessage(
        content=(
            f"Conversation summary:\n{summary}\n\n"
            "Active financial profile entries:\n"
            f"{serialized_profile if profile else 'No Active entries found.'}\n\n"
            "Effective interaction profile:\n"
            f"{serialized_interaction}"
        )
    )


def _interaction_context(profile: InteractionProfile) -> dict[str, JsonValue]:
    return {
        "tone": profile.tone.value,
        "banter": profile.banter.value,
        "verbosity": profile.verbosity.value,
        "coaching_style": profile.coaching_style.value,
        "proactivity": profile.proactivity.value,
        "language": profile.language.value,
        "warnings": list(profile.warnings),
    }


def build_conversation_graph(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    checkpointer: BaseCheckpointSaver[str] | None = None,
    *,
    today: date | None = None,
    currency: Currency = DEFAULT_CURRENCY,
    profile_service: FinancialProfileService | None = None,
    context_policy: ContextPolicy | None = None,
) -> ConversationGraph:
    """Build one assistant loop with profile loading, compaction, and guarded tools."""

    policy = context_policy or ContextPolicy()
    bound_model = model.bind_tools(list(tools))

    async def manage_context(state: FinanceConversationState) -> dict[str, Any]:
        """Refresh durable context and compact only complete older conversation turns."""

        updates: dict[str, Any] = {}
        if profile_service is not None:
            entries = await profile_service.active_entries(limit=policy.max_profile_entries)
            finance_entries = tuple(
                entry
                for entry in entries
                if not is_interaction_entry(entry)
                and not is_operational_context_entry(entry)
            )
            updates["financial_profile"] = _compact_profile(
                finance_entries, policy.max_profile_characters
            )
            updates["interaction_profile"] = _interaction_context(
                compile_interaction_profile(entries)
            )
        else:
            updates["financial_profile"] = []
            updates["interaction_profile"] = _interaction_context(InteractionProfile())

        messages = state["messages"]
        previous_summary = state.get("conversation_summary", "")
        token_messages: list[BaseMessage] = list(messages)
        if previous_summary:
            token_messages.append(SystemMessage(content=previous_summary))
        cutoff = _recent_turn_cutoff(messages, policy.keep_recent_user_turns)
        if (
            cutoff is not None
            and count_tokens_approximately(token_messages) >= policy.compact_after_tokens
        ):
            older_messages = list(messages[:cutoff])
            summary_request = HumanMessage(
                content=(
                    f"{SUMMARY_PROMPT}\n\n"
                    f"Existing summary:\n{previous_summary or 'None'}"
                )
            )
            response = await model.ainvoke(
                [SystemMessage(content=SUMMARY_PROMPT), *older_messages, summary_request]
            )
            if not isinstance(response, AIMessage):
                raise TypeError("The summarizer must return an AIMessage with text content")
            summary = response.text.strip()
            if not summary:
                raise TypeError("The summarizer must return an AIMessage with text content")
            updates["conversation_summary"] = summary
            updates["messages"] = [
                RemoveMessage(id=message.id)
                for message in older_messages
                if message.id is not None
            ]
        return updates

    async def assistant(state: FinanceConversationState) -> dict[str, list[AIMessage]]:
        current_date = today or date.today()
        prompt = SystemMessage(
            content=(
                f"{SYSTEM_PROMPT}\n"
                f"Currency policy: every unlabeled monetary amount is {currency.value}. "
                f"Display money with the {currency.symbol} symbol and never use $ unless a "
                "source explicitly identifies a different currency.\n"
                f"Today is {current_date.isoformat()}."
            )
        )
        response = await bound_model.ainvoke(
            [prompt, _context_message(state), *state["messages"]]
        )
        if not isinstance(response, AIMessage):
            raise TypeError("The chat model must return an AIMessage")
        return {"messages": [response]}

    builder = StateGraph(FinanceConversationState)
    builder.add_node("manage_context", manage_context)
    builder.add_node("assistant", assistant)
    builder.add_node("tools", ToolNode(list(tools)))
    builder.add_edge(START, "manage_context")
    builder.add_edge("manage_context", "assistant")
    builder.add_conditional_edges("assistant", tools_condition, {"tools": "tools", END: END})
    builder.add_edge("tools", "assistant")
    return builder.compile(checkpointer=checkpointer)


# Compatibility name for callers that pass only the safe read/draft catalog.
build_read_only_conversation_graph = build_conversation_graph


def _result_from_state(state: Mapping[str, Any]) -> ConversationTurnResult:
    interrupts = state.get("__interrupt__")
    if isinstance(interrupts, Sequence) and interrupts:
        value = getattr(interrupts[0], "value", None)
        return ConversationTurnResult(approval_request=value)
    messages = state.get("messages")
    if not isinstance(messages, list) or not messages:
        raise TypeError("Conversation returned no messages")
    response = messages[-1]
    if not isinstance(response, AIMessage):
        raise TypeError("Conversation did not finish with an AIMessage")
    return ConversationTurnResult(response=response)


async def start_conversation_turn(
    graph: ConversationGraph,
    *,
    thread_id: str,
    message: str,
    callbacks: Sequence[Any] = (),
) -> ConversationTurnResult:
    """Start a turn and surface an approval request instead of hiding an interrupt."""

    if not message.strip():
        raise ValueError("message cannot be empty")
    input_state: FinanceConversationState = {
        "messages": [HumanMessage(content=message)]
    }
    config: RunnableConfig = {
        **conversation_config(thread_id),
        "recursion_limit": 10,
    }
    if callbacks:
        config["callbacks"] = list(callbacks)
    state = await graph.ainvoke(input_state, config=config)
    return _result_from_state(state)


async def resume_approval(
    graph: ConversationGraph,
    *,
    thread_id: str,
    approved: bool,
    callbacks: Sequence[Any] = (),
) -> ConversationTurnResult:
    """Resume any currently interrupted write with an explicit human decision."""

    decision = {"action": "approve" if approved else "reject"}
    config: RunnableConfig = {
        **conversation_config(thread_id),
        "recursion_limit": 10,
    }
    if callbacks:
        config["callbacks"] = list(callbacks)
    state = await graph.ainvoke(Command(resume=decision), config=config)
    return _result_from_state(state)


async def resume_profile_write(
    graph: ConversationGraph, *, thread_id: str, approved: bool
) -> ConversationTurnResult:
    """Compatibility wrapper for the original financial-profile approval helper."""

    return await resume_approval(graph, thread_id=thread_id, approved=approved)


async def send_message(
    graph: ConversationGraph, *, thread_id: str, message: str
) -> AIMessage:
    """Compatibility helper for turns that are guaranteed not to request a write."""

    result = await start_conversation_turn(graph, thread_id=thread_id, message=message)
    if result.response is None:
        raise RuntimeError("Conversation paused for approval; use start_conversation_turn")
    return result.response
