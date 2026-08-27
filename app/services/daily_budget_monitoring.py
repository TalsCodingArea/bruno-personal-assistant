"""Application service connecting monitoring reads to deterministic analysis."""

from datetime import date

from app.domain.monitoring import DailyBudgetMonitoringReport
from app.services.daily_budget_analysis import analyze_daily_budget_state
from app.services.ports import MonitoringInputLoader


class DailyBudgetMonitoringService:
    """Produce one complete daily report without persistence or mutation."""

    def __init__(self, inputs: MonitoringInputLoader) -> None:
        self.inputs = inputs

    async def run(self, as_of: date) -> DailyBudgetMonitoringReport:
        """Load authoritative inputs and calculate a read-only report."""

        snapshot = await self.inputs.load(as_of)
        expected_month = as_of.replace(day=1)
        if snapshot.as_of != as_of or snapshot.month != expected_month:
            raise ValueError(
                "Monitoring input snapshot does not match the requested analysis date"
            )
        analysis = analyze_daily_budget_state(
            snapshot.transactions,
            snapshot.budgets,
            as_of,
            snapshot.monthly_income,
            policy=snapshot.policy,
        )
        return DailyBudgetMonitoringReport(inputs=snapshot, analysis=analysis)
