"""Independent event-driven LangGraph for expense monitoring."""

from datetime import date
from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from financial_agent.domain.expense_monitoring import (
    AlertDeliveryReceipt,
    BudgetReallocationDraft,
    ExpenseAlertEvaluation,
    ExpenseChangedEvent,
    ExpenseDiff,
    ExpenseImpact,
    ExpenseImpactAssessment,
    ExpenseMonitorCommit,
    MonitoringDecision,
    PendingAlertDelivery,
)
from financial_agent.domain.monitoring import MonitoringInputSnapshot
from financial_agent.services.expense_monitor_workflow import (
    ExpenseMonitorWorkflowService,
    parse_expense_event,
)


class ExpenseMonitorState(TypedDict):
    """Trace state kept separate from conversation messages and history."""

    event_id: str
    notion_page_id: str
    observed_at: str
    event_type: str
    event: NotRequired[ExpenseChangedEvent]
    duplicate_event: NotRequired[bool]
    decision: NotRequired[MonitoringDecision]
    diff: NotRequired[ExpenseDiff]
    financial_state: NotRequired[tuple[MonitoringInputSnapshot, ...]]
    impact: NotRequired[ExpenseImpact]
    assessments: NotRequired[tuple[ExpenseImpactAssessment, ...]]
    alert_evaluations: NotRequired[tuple[ExpenseAlertEvaluation, ...]]
    reallocation_drafts: NotRequired[tuple[BudgetReallocationDraft, ...]]
    commit: NotRequired[ExpenseMonitorCommit]
    pending_deliveries: NotRequired[tuple[PendingAlertDelivery, ...]]
    delivery_receipts: NotRequired[tuple[AlertDeliveryReceipt, ...]]


ExpenseMonitorGraph = CompiledStateGraph[
    ExpenseMonitorState,
    None,
    ExpenseMonitorState,
    ExpenseMonitorState,
]


def build_expense_monitor_graph(
    workflow: ExpenseMonitorWorkflowService,
) -> ExpenseMonitorGraph:
    """Build a retry-safe top-level graph with no conversation-state dependency."""

    async def validate_event(state: ExpenseMonitorState) -> dict[str, object]:
        event = parse_expense_event(
            event_id=state.get("event_id"),
            notion_page_id=state.get("notion_page_id"),
            observed_at=state.get("observed_at"),
            event_type=state.get("event_type"),
        )
        return {"event": event}

    async def deduplicate_event(state: ExpenseMonitorState) -> dict[str, object]:
        event = _event(state)
        existing = await workflow.existing_decision(event.event_id)
        if existing is None:
            return {"duplicate_event": False}
        return {"duplicate_event": True, "decision": existing}

    def deduplication_route(state: ExpenseMonitorState) -> str:
        return "deliver_alerts" if state.get("duplicate_event") else "resolve_diff"

    async def resolve_diff(state: ExpenseMonitorState) -> dict[str, object]:
        return {"diff": await workflow.resolve(_event(state))}

    async def load_financial_state(
        state: ExpenseMonitorState,
    ) -> dict[str, object]:
        snapshots = await workflow.load_financial_state(_diff(state))
        return {
            "financial_state": tuple(
                snapshots[month] for month in sorted(snapshots)
            )
        }

    async def calculate_impact(state: ExpenseMonitorState) -> dict[str, object]:
        return {
            "impact": workflow.calculate(_diff(state), _snapshots(state))
        }

    async def classify_severity(state: ExpenseMonitorState) -> dict[str, object]:
        return {
            "assessments": workflow.classify(_impact(state), _snapshots(state))
        }

    async def evaluate_response(state: ExpenseMonitorState) -> dict[str, object]:
        assessments = state.get("assessments", ())
        evaluations = await workflow.evaluate_alerts(
            _event(state), assessments, _snapshots(state)
        )
        return {"alert_evaluations": evaluations}

    async def draft_budget_response(
        state: ExpenseMonitorState,
    ) -> dict[str, object]:
        drafts = workflow.draft_reallocations(
            _diff(state),
            state.get("assessments", ()),
            _snapshots(state),
        )
        return {"reallocation_drafts": drafts}

    async def persist_decision(state: ExpenseMonitorState) -> dict[str, object]:
        commit = await workflow.commit(
            _diff(state),
            _impact(state),
            state.get("assessments", ()),
            _snapshots(state),
            state.get("alert_evaluations", ()),
            state.get("reallocation_drafts", ()),
        )
        return {"commit": commit, "decision": commit.decision}

    async def deliver_alerts(state: ExpenseMonitorState) -> dict[str, object]:
        event = _event(state)
        deliveries = await workflow.pending_deliveries(event.event_id)
        receipts = await workflow.deliver(deliveries)
        return {
            "pending_deliveries": deliveries,
            "delivery_receipts": receipts,
        }

    builder = StateGraph(ExpenseMonitorState)
    builder.add_node("validate_event", validate_event)
    builder.add_node("deduplicate_event", deduplicate_event)
    builder.add_node("resolve_diff", resolve_diff)
    builder.add_node("load_financial_state", load_financial_state)
    builder.add_node("calculate_impact", calculate_impact)
    builder.add_node("classify_severity", classify_severity)
    builder.add_node("evaluate_response", evaluate_response)
    builder.add_node("draft_budget_response", draft_budget_response)
    builder.add_node("persist_decision", persist_decision)
    builder.add_node("deliver_alerts", deliver_alerts)
    builder.add_edge(START, "validate_event")
    builder.add_edge("validate_event", "deduplicate_event")
    builder.add_conditional_edges(
        "deduplicate_event",
        deduplication_route,
        {
            "resolve_diff": "resolve_diff",
            "deliver_alerts": "deliver_alerts",
        },
    )
    builder.add_edge("resolve_diff", "load_financial_state")
    builder.add_edge("load_financial_state", "calculate_impact")
    builder.add_edge("calculate_impact", "classify_severity")
    builder.add_edge("classify_severity", "evaluate_response")
    builder.add_edge("evaluate_response", "draft_budget_response")
    builder.add_edge("draft_budget_response", "persist_decision")
    builder.add_edge("persist_decision", "deliver_alerts")
    builder.add_edge("deliver_alerts", END)
    return builder.compile()


def _event(state: ExpenseMonitorState) -> ExpenseChangedEvent:
    try:
        return state["event"]
    except KeyError as exc:
        raise RuntimeError("Validated expense event is unavailable") from exc


def _diff(state: ExpenseMonitorState) -> ExpenseDiff:
    try:
        return state["diff"]
    except KeyError as exc:
        raise RuntimeError("Expense diff is unavailable") from exc


def _impact(state: ExpenseMonitorState) -> ExpenseImpact:
    try:
        return state["impact"]
    except KeyError as exc:
        raise RuntimeError("Expense impact is unavailable") from exc


def _snapshots(state: ExpenseMonitorState) -> dict[date, MonitoringInputSnapshot]:
    return {snapshot.month: snapshot for snapshot in state.get("financial_state", ())}
