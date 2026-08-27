# Event-driven expense monitor

`expense_monitor` is a third top-level LangGraph. It starts from an expense event and never
loads conversation messages or shares a conversation checkpoint.

```text
expense event
  → validate and check event ledger
  → retrieve exact Notion page
  → diff against last processed page snapshot
  → load only affected monthly finance state
  → calculate deterministic regular/discrete/variable impact
  → classify severity and apply alert cooldown policy
  → optionally draft a bounded total-preserving reallocation
  → atomically persist decision, snapshot, alert state, and outbox
  → optional trace-only notification stub
```

## Trigger contract

The transport remains deliberately unconfigured. `UnconfiguredExpenseTrigger` defines the
boundary without pretending a webhook or polling job exists. Invoke the graph directly with:

```json
{
  "event_id": "notion-event-unique-id",
  "notion_page_id": "expense-page-id",
  "observed_at": "2026-08-27T12:00:00+03:00",
  "event_type": "created"
}
```

`event_type` accepts `created`, `updated`, or `deleted`. The timestamp must contain a timezone
offset. Created/updated events retrieve exactly the supplied page ID. Deleted events use the
last committed snapshot and then remove it after the decision is committed.

## Idempotency and edited expenses

The local operational ledger defaults to `.langgraph_api/expense-monitor.sqlite3`. It is
separate from conversation checkpoints, Financial Rules, and Notion finance databases. SQLite
is appropriate for one local Agent Server process; a multi-instance deployment should replace
the `ExpenseMonitorLedger` port with PostgreSQL.

The ledger stores:

- one decision per trigger `event_id`;
- one fingerprinted snapshot per Notion expense page;
- current monthly alert state per budget/variable scope;
- a transactional notification outbox.

An exact event retry returns the committed decision and performs no financial reads. A new
event whose page fingerprint is unchanged records a no-op decision. An edit that changes amount,
date, or classification removes the previous contribution and adds the current contribution.
For example, Groceries ₪100 → Takeout ₪125 becomes `Groceries -100`, `Takeout +125`, with a net
monthly change of ₪25. Moving an expense between months loads and evaluates both months.

## Deterministic impact and severity

Monthly state reuses `DailyMonitoringInputService`, so the event graph reads authoritative
`Final` transactions, budgets, Progressive, Volatility, recorded income, controlled monitoring
rules, and relevant rule provenance without conversation history.

- Accumulated budgets compare actual spending with calendar-day expected pace and projection.
- Discrete budgets compare against the full allocation and are never daily-pace projected.
- Unbudgeted, uncategorized, and ambiguous expenses compare with the remaining variable pool.
- The emergency buffer is subtracted from the variable pool through the controlled
  `monitoring.emergency_buffer_amount` rule.

The default policy is:

| Setting | Default |
| --- | --- |
| Projection bands | 25%, 50%, 75% |
| Minimum material amount | ₪25 |
| Alert cooldown | 24 hours |
| Material projected-variance increase | ₪50 |
| Reallocation draft enabled | yes |
| Maximum reallocation draft | ₪150 |
| Protected subcategories | Rent, Groceries, Electricity |

An actual budget overspend is critical. Projection bands map to watch, warning, and critical;
smaller material projections are informational. A threatened protected constraint is promoted
to critical. Variable-pool deficits are warning or critical according to material size.

Alerts normally recur only after severity escalation, material projected-variance growth,
cooldown expiry, recovery followed by regression, or a protected constraint threat. Alert state
and the proposed alert are committed with the monitoring decision before delivery, preventing
duplicate sends after a crash. A pending outbox item is retried when the same event is invoked.

## Rollout modes

Set `FINANCE_AGENT_EXPENSE_MONITOR_MODE` to one of:

| Mode | Behavior |
| --- | --- |
| `observe` | Diff, load, calculate, classify, and persist; produce no alert evaluation |
| `shadow` | Default; propose alerts in traces, but enqueue and send nothing |
| `draft` | Shadow behavior plus safe current-month reallocation drafts |
| `trace_delivery` | Draft behavior plus transactional delivery to the trace-only stub |

`trace_delivery` does not contact a person. Its receipt explicitly says that the notification
was written only to the LangSmith trace. A real email/push/chat adapter must implement
`ExpenseAlertNotifier` before external delivery is enabled.

## Budget response boundary

Draft generation uses the existing deterministic donor constraints. It only proposes a change
when the triggering event moves a current-month category from within budget to actual
overspend. It never reduces protected, discrete, zero-volatility, or unknown-volatility
budgets. It uses only explicit donor-to-target transfers, caps the amount, and proves the total
allocation is unchanged.

No mode mutates a Budget page. Approval-gated persistence is intentionally deferred until the
Budget Adjustments data-source property contract is chosen. This avoids overwriting the
original plan or silently moving the goalposts.

## Conversation reconciliation

Every decision records the source expense, affected months/scopes, referenced Budget page IDs,
controlled Financial Rule keys, calculation version, severity, and summary. The conversation
graph can retrieve these records on demand with `get_expense_monitoring_decisions`, filtered by
event ID, Notion page ID, or month. They are not injected into every prompt.

This lets a later correction distinguish a wrong Notion classification/Progressive value from
an explanation mistake, rule change, one-time exception, or calculation defect without mixing
the two graphs' state.

## Known boundaries

- The actual webhook/poller and real alert channel are stubs.
- Future Expenses has no confirmed Notion property mapping, so planned-expense allocations are
  not yet subtracted separately. Savings already represented by monthly Budget rows remain in
  the allocation total.
- SQLite should be replaced by a shared durable store before horizontal deployment.
- Budget adjustment approval, append-only adjustment persistence, and automatic adjustments are
  not exposed by this graph.
