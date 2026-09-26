# Bruno Personal Assistant

Bruno is the outer personal-assistant runtime. It owns Telegram channels, streaming, logging,
receipt handling, trusted automations, durable routing context, and capability selection.
Financial reasoning is delegated to the finance capability under
[`Capabilities/financial-agent`](Capabilities/financial-agent/README.md).

The dependency direction is intentional: `bruno` imports `financial_agent`, while the
capability never imports `bruno`. Future capabilities can be added beside the financial agent
without sharing a generic Python package name.

Bank-account movement ingestion is intentionally separate from the agent runtime under
[`bank_account`](bank_account/README.md). It reads bank Excel exports from a dedicated inbox,
writes them to the Bank Movement Notion database, and removes a file only after a complete,
idempotent import.

```text
Bruno/
├── bruno/                         # Telegram shell and capability coordinator
├── bank_account/                  # Standalone Excel-to-Notion bank importer
├── Capabilities/
│   └── financial-agent/
│       ├── financial_agent/       # Finance graph, services, integrations, and tools
│       ├── docs/
│       ├── langgraph.json          # Graphs exposed by this capability
│       └── README.md
├── tests/
└── pyproject.toml
```

## Runtime shape

```text
Telegram channel
      |
      v
one-node capability selector ----> general (placeholder)
      |
      +---------------------------> finance conversation graph

receipts/automations --> trusted finance automation tools --> Notion
                                      |
                                      v
                          10-minute trailing-edge debounce
                                      |
                                      v
                      daily analysis + guarded rebudget graph
                                      |
                                      v
                              Telegram notification
```

The selector has its own durable LangGraph thread per Telegram chat. Finance has a separate
thread, so its checkpoints and approval interrupts survive routing turns. The general
capability is intentionally a placeholder until another capability graph is connected.

## Run

Create the environment from the Bruno root, copy `.env.example`, and fill in the Telegram,
OpenAI, and Notion values:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
```

Then run:

```bash
.venv/bin/python -m bruno.app
```

## Run continuously with Docker

The production Compose service runs the Telegram bot as a non-root process, restarts it after
an unexpected exit, retries transient Telegram bootstrap failures, limits Docker log growth,
and allows a graceful 45-second shutdown. The container filesystem is read-only except for
temporary receipt processing and the `bruno-data` volume.

Create `.env` from the example, fill in its secrets and IDs, then build and start Bruno:

```bash
cp .env.example .env
docker compose up -d --build
```

Useful operating commands:

```bash
docker compose ps
docker compose logs --follow --tail=100 bruno
docker compose restart bruno
docker compose down
```

`docker compose down` preserves the named volume. It contains both the conversation checkpoints
and expense-monitor ledger. Do not run `docker compose down --volumes` unless you intentionally
want to delete that state. Back up the `bruno-data` volume before host or Docker migrations.

On a Mac mini, configure macOS not to sleep automatically and configure Docker Desktop to start
at login; Compose can restart Bruno only while the Docker engine itself is running. See the
[Mac Mini deployment guide](docs/mac-mini-deployment.md) for secure first-time setup and the
host-side update workflow.

## Test one capability in LangGraph Studio

Start the in-memory LangGraph development server for the financial capability:

```bash
.venv/bin/python -m bruno capability-dev financial-agent
```

The server prints its API, documentation, and LangSmith Studio URLs. Telegram credentials are
not used by this command. Standard `langgraph dev` options are forwarded after the capability
name, for example:

```bash
.venv/bin/python -m bruno capability-dev financial-agent --port 2025 --no-browser
```

Each future folder under `Capabilities/` becomes independently testable by adding its own
`langgraph.json`; the Bruno command discovers configurations instead of maintaining a central
graph list.

The four configured Telegram chats are allow-listed by exact chat ID:

- personal assistant: capability routing and streamed finance conversations;
- receipts: PDF receipt extraction, invoice upload, and expense creation;
- automations: explicit JSON automation messages;
- logs: best-effort operational errors.

An existing Bruno-style automation payload remains accepted:

```json
{
  "tool": "log_expense",
  "args": {
    "Description": "Coffee",
    "Amount": 14.5,
    "Date": "2026-08-31",
    "Category": "Food",
    "Timezone": "GMT+03:00"
  }
}
```

`Timezone` defaults to the fixed offset `GMT+03:00`. Timestamp inputs are converted to that
offset before Bruno chooses the expense date.

`check_expenses` may also be sent through the automation chat. It schedules a check rather
than running immediately.

## Expense check-up behavior

Before creating an expense from a receipt, Bruno loads expenses from the receipt date. If an
existing expense has exactly the same amount, Bruno adds the PDF to that expense regardless of
the expense description or receipt vendor name. Otherwise Bruno creates a new expense.

Every newly created receipt expense or `log_expense` call schedules the same trailing-edge check.
A new expense within `BRUNO_EXPENSE_CHECKUP_DELAY_SECONDS` replaces the pending check, so a burst
of expenses normally produces one analysis after ten quiet minutes.

The check uses the finance agent's deterministic daily budget graph. It can react to:

- actual category overspend;
- a material projected overrun for an `Accumulated` budget;
- a negative variable-expense reserve;
- budgets exceeding income or missing income.

`Discrete` budgets are not pace-projected. Automatic budget changes use the graph's existing
fresh-read, rollback-capable mutation path and occur only when the active Financial Rule
`monitoring.automatic_budget_adjustments_enabled` has JSON value `true`. Otherwise Bruno sends
the proposed warning without writing. A savings warning is emitted only for the residual gap
that cannot be covered by remaining income or allowable budget reallocations.

The debounce is process-local. Durable checkpoints preserve conversations, but a pending
ten-minute timer does not survive a bot restart; move this timer to a durable job queue before
running multiple Bruno replicas.

## Verify

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check bruno bank_account Capabilities/financial-agent/financial_agent tests
.venv/bin/python -m mypy bruno bank_account Capabilities/financial-agent/financial_agent
```
