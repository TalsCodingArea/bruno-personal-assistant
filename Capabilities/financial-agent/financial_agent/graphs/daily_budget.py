"""Standalone LangGraph orchestration for one daily budget-monitoring run."""

from datetime import date
from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from financial_agent.domain.budget_mutation import (
    BudgetMutationApplyError,
    BudgetMutationFreshnessError,
    BudgetMutationPartialFailure,
    BudgetMutationResult,
    BudgetMutationStatus,
    BudgetPreferenceReviewRejected,
)
from financial_agent.domain.daily_budget_workflow import (
    DailyBudgetMutationDisposition,
    DailyBudgetMutationOutcome,
)
from financial_agent.domain.monitoring import DailyBudgetMonitoringReport
from financial_agent.domain.operational_context import (
    OperationalContextEntry,
    OperationalContextPersistenceResult,
)
from financial_agent.services.ports import (
    BudgetMutationRunner,
    DailyBudgetMonitoringRunner,
    OperationalContextManager,
)


class DailyBudgetWorkflowState(TypedDict):
    """Traceable state for an independent, idempotent daily run."""

    as_of: str
    report: NotRequired[DailyBudgetMonitoringReport]
    context: NotRequired[OperationalContextPersistenceResult]
    mutation_outcome: NotRequired[DailyBudgetMutationOutcome]
    applied_context: NotRequired[tuple[OperationalContextEntry, ...]]


DailyBudgetGraph = CompiledStateGraph[
    DailyBudgetWorkflowState,
    None,
    DailyBudgetWorkflowState,
    DailyBudgetWorkflowState,
]


def build_daily_budget_graph(
    monitoring: DailyBudgetMonitoringRunner,
    operational_context: OperationalContextManager,
    budget_mutation: BudgetMutationRunner,
) -> DailyBudgetGraph:
    """Compose existing boundaries without moving business rules into graph nodes."""

    async def analyze(state: DailyBudgetWorkflowState) -> dict[str, object]:
        try:
            as_of = date.fromisoformat(state["as_of"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("as_of must be an ISO date such as 2026-08-27") from exc
        report = await monitoring.run(as_of)
        return {"report": report}

    async def reconcile_context(
        state: DailyBudgetWorkflowState,
    ) -> dict[str, object]:
        report = _report(state)
        return {"context": await operational_context.reconcile_report(report)}

    async def decide_mutation(
        state: DailyBudgetWorkflowState,
    ) -> dict[str, object]:
        report = _report(state)
        plan = report.analysis.adjustment_plan
        if plan is None or not plan.budget_changes:
            return {
                "mutation_outcome": DailyBudgetMutationOutcome(
                    disposition=DailyBudgetMutationDisposition.NOT_NEEDED,
                    reason="The deterministic analysis proposed no Budget DB changes.",
                )
            }
        if not report.inputs.policy.automatic_budget_adjustments_enabled:
            return {
                "mutation_outcome": DailyBudgetMutationOutcome(
                    disposition=DailyBudgetMutationDisposition.DISABLED,
                    reason=(
                        "A rebalance is available, but the Active Financial Rule for "
                        "automatic budget adjustments is not enabled."
                    ),
                )
            }
        return {}

    def mutation_route(state: DailyBudgetWorkflowState) -> str:
        return "end" if "mutation_outcome" in state else "mutate"

    async def mutate(state: DailyBudgetWorkflowState) -> dict[str, object]:
        report = _report(state)
        try:
            result = await budget_mutation.apply_report(report)
        except BudgetMutationPartialFailure as exc:
            return {"mutation_outcome": _error_outcome(
                DailyBudgetMutationDisposition.PARTIAL_FAILURE, exc
            )}
        except BudgetMutationApplyError as exc:
            return {"mutation_outcome": _error_outcome(
                DailyBudgetMutationDisposition.FAILED_ROLLED_BACK, exc
            )}
        except (BudgetMutationFreshnessError, BudgetPreferenceReviewRejected) as exc:
            return {"mutation_outcome": _error_outcome(
                DailyBudgetMutationDisposition.BLOCKED, exc
            )}
        disposition = (
            DailyBudgetMutationDisposition.APPLIED
            if result.status is BudgetMutationStatus.APPLIED
            else DailyBudgetMutationDisposition.ALREADY_APPLIED
            if result.status is BudgetMutationStatus.ALREADY_APPLIED
            else DailyBudgetMutationDisposition.NOT_NEEDED
        )
        return {
            "mutation_outcome": DailyBudgetMutationOutcome(
                disposition=disposition,
                reason=_success_reason(result),
                result=result,
            )
        }

    def applied_context_route(state: DailyBudgetWorkflowState) -> str:
        outcome = state["mutation_outcome"]
        if outcome.disposition in {
            DailyBudgetMutationDisposition.APPLIED,
            DailyBudgetMutationDisposition.ALREADY_APPLIED,
        }:
            return "record_applied_context"
        return "end"

    async def record_applied_context(
        state: DailyBudgetWorkflowState,
    ) -> dict[str, object]:
        outcome = state["mutation_outcome"]
        if outcome.result is None:
            raise RuntimeError("Confirmed mutation outcome is missing its result")
        entries = await operational_context.record_budget_adjustment(
            _report(state), outcome.result
        )
        return {"applied_context": entries}

    builder = StateGraph(DailyBudgetWorkflowState)
    builder.add_node("analyze", analyze)
    builder.add_node("reconcile_context", reconcile_context)
    builder.add_node("decide_mutation", decide_mutation)
    builder.add_node("mutate", mutate)
    builder.add_node("record_applied_context", record_applied_context)
    builder.add_edge(START, "analyze")
    builder.add_edge("analyze", "reconcile_context")
    builder.add_edge("reconcile_context", "decide_mutation")
    builder.add_conditional_edges(
        "decide_mutation", mutation_route, {"mutate": "mutate", "end": END}
    )
    builder.add_conditional_edges(
        "mutate",
        applied_context_route,
        {"record_applied_context": "record_applied_context", "end": END},
    )
    builder.add_edge("record_applied_context", END)
    return builder.compile()


def _report(state: DailyBudgetWorkflowState) -> DailyBudgetMonitoringReport:
    try:
        return state["report"]
    except KeyError as exc:
        raise RuntimeError("Daily workflow report is unavailable") from exc


def _error_outcome(
    disposition: DailyBudgetMutationDisposition, error: Exception
) -> DailyBudgetMutationOutcome:
    return DailyBudgetMutationOutcome(
        disposition=disposition,
        reason=f"{type(error).__name__}: {error}",
    )


def _success_reason(result: BudgetMutationResult) -> str:
    if result.status is BudgetMutationStatus.APPLIED:
        return f"Confirmed {len(result.items)} Budget DB page change(s)."
    if result.status is BudgetMutationStatus.ALREADY_APPLIED:
        return "The same idempotent Budget DB operation was already confirmed."
    return "The verified mutation contained no Budget DB changes."
