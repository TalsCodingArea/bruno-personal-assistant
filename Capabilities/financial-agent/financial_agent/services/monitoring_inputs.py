"""Read-only orchestration for daily budget-monitoring inputs."""

import asyncio
from datetime import date

from financial_agent.domain.interaction import is_interaction_entry
from financial_agent.domain.money import ZERO, money
from financial_agent.domain.monitoring import MonitoringGuideline, MonitoringInputSnapshot
from financial_agent.domain.operational_context import is_operational_context_entry
from financial_agent.services.monitoring_policy import (
    MONITORING_POLICY_KEYS,
    compile_monitoring_policy,
)
from financial_agent.services.ports import ActiveFinancialRulesReader, FinanceReader


class DailyMonitoringInputService:
    """Load a bounded Notion snapshot without calculation or side effects."""

    def __init__(
        self,
        finance: FinanceReader,
        rules: ActiveFinancialRulesReader,
        *,
        max_rule_entries: int = 100,
    ) -> None:
        if max_rule_entries <= 0:
            raise ValueError("max_rule_entries must be positive")
        self.finance = finance
        self.rules = rules
        self.max_rule_entries = max_rule_entries

    async def load(self, as_of: date) -> MonitoringInputSnapshot:
        """Load only data observed from the first of the month through ``as_of``."""

        month = as_of.replace(day=1)
        transactions, incomes, budgets, entries = await asyncio.gather(
            self.finance.transactions(month, as_of),
            self.finance.incomes(month, as_of),
            self.finance.budgets(month),
            self.rules.active_entries(limit=self.max_rule_entries),
        )
        compiled = compile_monitoring_policy(entries)
        guidelines = tuple(
            MonitoringGuideline(
                key=entry.key,
                statement=entry.statement,
                kind=entry.kind.value,
                scopes=entry.scopes,
                page_id=entry.page_id,
                operation_id=entry.operation_id,
                last_edited_at=entry.last_edited_at,
            )
            for entry in entries
            if not is_interaction_entry(entry)
            and not is_operational_context_entry(entry)
            and entry.key.strip().casefold() not in MONITORING_POLICY_KEYS
            and any(scope.casefold() == "budget monitoring" for scope in entry.scopes)
        )
        monthly_income = (
            money(sum((income.amount for income in incomes), ZERO)) if incomes else None
        )
        return MonitoringInputSnapshot(
            month=month,
            as_of=as_of,
            transactions=transactions,
            incomes=incomes,
            budgets=budgets,
            monthly_income=monthly_income,
            policy=compiled.policy,
            policy_sources=compiled.sources,
            guidelines=guidelines,
        )
