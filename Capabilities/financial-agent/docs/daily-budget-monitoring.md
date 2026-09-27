# Daily budget monitoring: deterministic foundation

This first layer answers one question: given already-loaded transactions, budgets,
income, a date, and policy rules, what is the current financial state and what bounded
budget movements would be mathematically valid?

It performs no I/O. It does not query or update Notion, run on a schedule, modify graph
state, or send an alert. Those are later orchestration layers.

## Classification

- A transaction whose single, exact subcategory has a current-month budget is regular.
- Every other transaction is variable. This includes unbudgeted, uncategorized, and
  multi-selected subcategories because the system must not guess which budget to use.
- Variable spending consumes the variable reserve.
- Savings are ordinary budget pages, so they are included in the sum of monthly budgets.

The starting variable reserve is:

`monthly income - sum of current-month budgets`

The remaining reserve before adjustments is:

`starting variable reserve - actual variable spending`

## Projection and observations

Accumulated budgets use calendar-day pacing:

`actual spend / elapsed calendar days * days in month`

Discrete budgets are not pace-projected. Their projected amount is the greater of actual
spend and budget, so they create an observation only after actual overspend.

The default projection bands are 25%, 50%, and 75% over budget. The analysis emits the
highest band currently crossed. A later context layer will decide whether that band was
already presented to the user and therefore whether it should be surfaced again.

## Adjustment order

Actual overspend and a material projected shortfall on an Accumulated budget produce a proposed
adjustment. Actual overspend also produces an alert draft. Funding is allocated in this order:

1. Cover a negative variable reserve by reducing eligible donor budgets.
2. Use any positive variable reserve for actual or material projected budget shortfalls.
3. Transfer eligible donor capacity to remaining shortfalls.
4. Leave any amount that cannot be funded as an explicit unresolved shortfall.

Protected targets are handled first, followed by the highest percentage overspend.
Protected budgets cannot donate. Missing volatility means a budget is not adjustable.
Only accumulated budgets are pace-projected or donate automatically in this layer; a discrete budget conserves
its full amount because calendar pacing cannot prove that its unused amount is safe.

An accumulated donor's controllable capacity is:

`max(budget - max(actual, projected), 0) * volatility percent`

Preferred donors are tried first, then other eligible donors by volatility and available
capacity. This preference is ordering, not permission to violate protection or volatility.

If monthly income is missing, the system still calculates projections and actual
overspends, but creates no adjustment plan. It emits a structured income-missing
observation for the future conversation context so the assistant can remind the user to
log income.

## Files in this layer

- `financial_agent/domain/monitoring.py` defines the input-independent result vocabulary.
- `financial_agent/services/daily_budget_analysis.py` contains the pure calculation.
- `tests/test_daily_budget_analysis.py` records fixed examples in ILS-safe `Decimal`
  arithmetic.

## Read-only input layer

`DailyMonitoringInputService` now loads one bounded snapshot through the existing Notion
adapters. Its four independent reads are performed concurrently:

1. Tal's transactions from the first of the month through the analysis date.
2. Income rows from the first of the month through the analysis date.
3. Budgets whose Date is in the current month.
4. Compact Active Financial Rules entries.

An empty income result becomes `None`, not zero. Multiple rows are summed with `Decimal` because
salary and additional cash deposits may both exist. The snapshot records compact page IDs,
operation IDs, and last-edited timestamps for applied policy overrides, but does not copy every
Financial Rule into monitoring state.

The controlled rule keys are:

| Key | Statement JSON | Default |
| --- | --- | --- |
| `monitoring.projection_bands` | `[25, 50, 75]` | `[25, 50, 75]` |
| `monitoring.protected_subcategories` | `["Rent", "Groceries", "Electricity"]` | same |
| `monitoring.preferred_donor_subcategories` | `["Takeout", "Entertainment"]` | `[]` |
| `monitoring.alert.minimum_material_amount` | `40` | `25` |
| `monitoring.alert.cooldown_hours` | `12` | `24` |
| `monitoring.alert.material_projection_increase` | `75` | `50` |
| `monitoring.reallocation.enabled` | `false` | `true` |
| `monitoring.reallocation.max_amount` | `100` | `150` |
| `monitoring.emergency_buffer_amount` | `2000` | `0` |

These are exact machine-readable contracts inside the otherwise human-readable Financial Rules
database. Missing entries use defaults. A malformed or duplicated Active controlled key stops
the monitoring run; it never silently falls back after the user has explicitly configured a
rule. Unrelated profile entries are ignored by the policy compiler.

This layer still performs no calculation, write, scheduling, context persistence, or alert
delivery.

## Read-to-calculation bridge

`DailyBudgetMonitoringService.run(as_of)` now performs one complete read-only run:

```text
DailyMonitoringInputService.load(as_of)
  → validate snapshot month and date
  → analyze_daily_budget_state(...)
  → DailyBudgetMonitoringReport(inputs, analysis)
```

The report deliberately retains both the immutable source snapshot and its analysis. Later
reconciliation and mutation validation will therefore be able to identify the exact budgets,
income, transactions, and policy versions behind a decision. The service does not catch or
hide input/configuration failures and does not store the report.

### Manual live inspection

From the project root, start a Python REPL with `.venv/bin/python`, then run:

```python
import asyncio
from datetime import date
from pprint import pprint

from financial_agent.bootstrap import build_finance_application
from financial_agent.tools.serialization import jsonable


async def inspect():
    application = build_finance_application()
    try:
        report = await application.daily_monitoring.run(date.today())
        pprint(jsonable(report), sort_dicts=False)
    finally:
        await application.aclose()


asyncio.run(inspect())
```

This reads live financial data and prints it only in the local terminal. It does not modify
Notion. The output contains transaction details, so it should not be pasted into public logs.

Operational context persistence, guarded budget mutation, and their standalone daily LangGraph
are now implemented around this report service. The service itself remains read-only, keeping
its report reusable for fresh verification. Scheduling, conversation injection, and alert
delivery remain separate layers.
