# Operational context persistence and reconciliation

Operational context is system-maintained, short-lived financial state. It is different from
durable Financial Rules:

- A Financial Rule says how Tal wants finances handled.
- Operational context says what the daily deterministic analysis currently observes.
- Conversation history records what was discussed, but is not financial truth.

Operational context uses the existing Financial Rules data source without adding properties.
Every operational page has:

- `Key`: reserved `monitoring.runtime.` namespace
- `Scope`: both `Operational Context` and `Budget Monitoring`
- `Kind`: `Decision` for compatibility with the existing select contract; this is not treated
  as a user-authored financial decision because Scope and Key identify it as runtime state
- `Statement`: compact schema-versioned JSON used for reconciliation
- `Status`: one current `Active` version; older system-maintained versions become `Archived`
- `Supersedes`: link to the immediate previous version
- `Operation ID`: automatic Notion identifier

The page body contains a human-readable lifecycle, evidence, and presentation summary. It is
informational; `Statement` remains authoritative. Operational pages should not be edited
manually.

## Stable identity

Each identity is monthly and condition-specific:

```text
monitoring.runtime.<YYYY-MM>.<kind>.<subcategory-hash-or-global>
```

The hash is based on the normalized exact subcategory. The readable subcategory remains in the
page name and JSON. Hashing prevents spaces, Hebrew text, emoji, or renaming rules from breaking
the profile key grammar.

Current kinds are:

- `projection_deviation`
- `actual_overspend`
- `income_missing`
- `variable_reserve_deficit`
- `budgets_exceed_income`

## Lifecycle

Reconciliation compares one `DailyBudgetMonitoringReport` with the current entries for that
month:

- New condition → `created`
- Same condition with newer evidence → `updated`
- Projection crosses a higher configured band → `escalated`
- Resolved condition appears again → `reactivated`
- Condition disappears from the report → `resolved`
- Exact same-day rerun with identical evidence → `unchanged`, with no write

Resolved business state remains the current Notion version. This preserves acknowledgement if
the same condition reappears during the month. Old monthly state is isolated by its key prefix;
a later retention layer may archive old months.

## Presentation signals

Every state contains `current_signal` and optional `acknowledged_signal`.

- Projection signals change only when the highest band changes, for example
  `projection_deviation:25`, then `projection_deviation:50`.
- Missing-income, actual-overspend, and reserve signals include the report date, so one latest
  reminder can be presented per daily run.
- `needs_presentation` is true only for Active state whose two signals differ.

The future conversation integration will present the state and then call
`acknowledge_signal(key, expected_signal)`. The expected signal is an optimistic concurrency
guard: a stale conversation cannot acknowledge a newer condition it did not show.

## Persistence guarantees

`OperationalContextService` is internal application infrastructure, not an agent tool. Its
writes therefore cannot be invoked through ordinary conversation. It creates a new version,
then marks its predecessor Archived. It verifies page identity and Notion's automatic
`last_edited_time` before replacement.

An exact retry is idempotent. If a previous attempt created the desired version but failed
before archiving its direct predecessor, retrying finishes that retirement. Ambiguous multiple
current versions fail closed during ordinary finance work. Bruno's general operations graph
exposes a dedicated repair tool: it groups Active operational pages by key, keeps the entry with
the newest business observation, records stale siblings in its `Supersedes` relation, and
archives them. The repair is idempotent and reports only confirmed changes.

Operational state records a `proposed_adjustment_amount`; it never labels that proposal as
applied. The daily graph persists a separate `budget_adjustment_applied` event only after the
guarded Notion writer confirms the final page state. A retry can repair a missing event without
applying the same Budget DB operation twice.

## Current boundary

Implemented:

```text
DailyBudgetMonitoringReport
  → pure reconcile_daily_report
  → OperationalContextService
  → NotionOperationalContextRepository
  → versioned Financial Rules pages
```

Not yet implemented:

- Loading presentable operational state into conversation context
- Calling acknowledgement after a successful assistant response
- Alert delivery
- Automatic invocation at 4:00 AM Asia/Jerusalem
- Old-month retention
