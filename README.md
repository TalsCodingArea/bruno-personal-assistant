# Finance Agent

A personal finance agent built with LangGraph and Notion.

The graphs can read financial data, perform deterministic calculations, manage compact
conversation context, and version durable financial preferences after explicit approval. Start
with the [architecture walkthrough](docs/architecture.md), [context walkthrough](docs/context-management.md),
[interaction profile walkthrough](docs/interaction-profile.md), and the
[Notion schema contract](docs/notion-schema.md). Daily runtime memory is described in the
[operational context walkthrough](docs/operational-context.md).
Guarded automatic writes are described in the
[budget mutation walkthrough](docs/budget-mutation.md).
The executable daily orchestration is described in the
[daily budget graph walkthrough](docs/daily-budget-workflow.md).
The independent event workflow is described in the
[expense monitor walkthrough](docs/expense-monitoring.md).

## Setup

Requirements: Python 3.11+ and preferably [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env
```

Add the Notion integration token to `.env`:

```dotenv
FINANCE_AGENT_NOTION_TOKEN=secret_...
FINANCE_AGENT_CURRENCY=ILS
FINANCE_AGENT_EXPENSES_DATA_SOURCE_ID=...
FINANCE_AGENT_INCOME_DATA_SOURCE_ID=...
FINANCE_AGENT_BUDGETS_DATA_SOURCE_ID=...
FINANCE_AGENT_FUTURE_EXPENSES_DATA_SOURCE_ID=...
FINANCE_AGENT_FINANCIAL_RULES_DATA_SOURCE_ID=...
```

The integration must be shared with each configured Notion data source. The chat model is
injected into the graph factory. The Studio entrypoint uses `gpt-5.6-terra` with medium
reasoning through OpenAI's Responses API.

ILS is a strict application policy. Unlabeled amounts from finance tools are ILS, and the
assistant renders user-facing money with `₪`, not `$`.

## LangSmith Studio

The development extra includes LangGraph's in-memory Agent Server. Configure non-empty
`OPENAI_API_KEY` and `LANGSMITH_API_KEY` values, then run:

```bash
.venv/bin/langgraph dev
```

The server prints the local API, documentation, and Studio URLs. Select the `finance_agent`
graph and create a thread. When `apply_financial_profile_update` is called, the run pauses with
an approval payload; resume it with `{"action": "approve"}` or `{"action": "reject"}`.

Select `daily_budget_monitor` to inspect one standalone daily run and submit an ISO date, for
example `{"as_of": "2026-08-27"}`. This graph uses live Notion data and can change current-month
budgets only when the automatic-adjustment Financial Rule is explicitly enabled.

Select `expense_monitor` to inspect one expense event. Its default `shadow` mode persists a
grounded decision and proposed alert in its separate operational ledger, but sends no external
notification and performs no Budget DB mutation.

Studio/Agent Server owns checkpoint persistence. `app/graphs/studio.py` deliberately compiles
the graph without the test application's checkpointer.

## Verify

```bash
uv run pytest
uv run ruff check .
uv run mypy app
```

## Layer map

```text
app/
├── domain/             # Plain finance objects and Decimal-safe money
├── integrations/       # notion-client adapter and Notion-to-domain mapping
├── services/
│   ├── calculations.py # Pure deterministic finance functions
│   ├── budget_mutation.py # Fresh-read and preference-gated writes
│   ├── daily_budget_monitoring.py # Read-to-analysis daily report
│   ├── finance_queries.py # Fetch + calculate use cases
│   ├── profile.py      # Durable profile versioning rules
│   ├── interaction.py  # Validated conversational preferences
│   ├── monitoring_inputs.py # Read-only daily Notion snapshot loader
│   ├── monitoring_policy.py # Financial Rules to monitoring policy
│   ├── expense_monitoring.py # Pure event diff, impact, severity, alert, and draft rules
│   ├── expense_monitor_workflow.py # Event reads, ledger/outbox, and delivery coordination
│   ├── operational_context.py # Versioned runtime-context persistence
│   ├── operational_reconciliation.py # Pure report reconciliation
│   └── ports.py        # Finance and profile persistence interfaces
├── tools/
│   ├── read/           # Factual tools
│   ├── draft/          # Non-persisting proposal tools
│   └── write/          # Tools that interrupt for explicit approval
├── graphs/
│   ├── conversation.py  # Assistant loop with context management
│   ├── daily_budget.py  # Standalone deterministic daily orchestration
│   └── expense_monitor.py # Independent event-driven expense orchestration
├── bootstrap.py         # Connect real dependencies
└── api/                 # Reserved for the transport layer
```

## Deterministic finance functions

- `monthly_category_summary`
- `uncategorized_review`
- `variable_spending_pool`
- `planned_expense_monthly_allocation`
- `month_end_forecast`
- `budget_status`
- `suggest_categories`
- `draft_category_updates`
- `draft_planned_expense`

All domain and calculation currency values are `Decimal`. Agent-facing tool schemas accept
currency as base-10 strings such as `"2000.00"`, then convert immediately to `Decimal`. This
keeps OpenAI function schemas compatible without introducing binary floating-point arithmetic.
The pure functions perform no I/O and have fixed sample tests.

Expense `Category` and `Sub Category` support Notion `multi_select`. Every value is preserved.
Exactly one selection becomes the canonical classification; multiple selections are surfaced
for review instead of choosing the first or double-counting the expense.

Budget status uses exact monthly sub-category membership: matched spending is `regular`, and
unmatched spending is `variable`. Accumulated regular budgets are pace-forecast; discrete
budgets reserve their full amount. Volatility determines the controllable portion of positive
remaining budget.

## Tool catalog

Read tools:

- `get_monthly_summary`
- `get_uncategorized_transactions`
- `suggest_categories`
- `get_budget_status`
- `forecast_month_end`
- `get_upcoming_planned_expenses`

Draft tools:

- `draft_category_updates`
- `draft_planned_expense`
- `draft_financial_profile_update`
- `draft_interaction_preference_update`

Preference read tools:

- `get_financial_profile`
- `get_interaction_profile`
- `get_expense_monitoring_decisions`

Approval-interrupted write tools:

- `apply_financial_profile_update`
- `apply_interaction_preference_update`

Draft tools return proposals only. The profile apply tool is included only in the approval-aware
catalog and call LangGraph `interrupt()` before touching Notion.

## Conversation persistence

The graph requires a LangGraph checkpointer and a non-empty `thread_id`. Before each new turn,
`manage_context` reloads Active profile entries, splits financial rules from `assistant.*`
interaction settings, and checks the approximate conversation size.
At 12,000 tokens it summarizes complete older turns, removes them from current message state,
and keeps the six most recent user turns intact. Tests use `InMemorySaver`; production still
needs a durable checkpointer and a separate checkpoint-retention policy.

## Known configuration gap

Future Expenses does not yet have a property mapping. Its read tool returns
`schema_not_configured` until the title, target amount, due date, and optional saved amount
properties are supplied. The allocation calculation and draft tool are already implemented
and tested independently of Notion.
