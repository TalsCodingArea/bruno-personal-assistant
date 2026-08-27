# Daily budget LangGraph

This graph is the first executable orchestration layer for autonomous budget management. It
composes the existing readers, deterministic calculation, operational reconciliation, and
guarded mutation boundary. It does not recalculate finance logic inside graph nodes.

## Node sequence

```text
{"as_of": "YYYY-MM-DD"}
  → analyze
      fresh Notion snapshot + deterministic DailyBudgetMonitoringReport
  → reconcile_context
      version current observations in Financial Rules
  → decide_mutation
      no change / disabled → end with an explicit outcome
      eligible             → mutate
  → mutate
      two fresh verifications + optional GPT preference veto + guarded Notion write
  → record_applied_context
      persist confirmed page-level adjustment events
  → end
```

The graph has no conversation messages and no checkpointer. Each run is independent. Durable
retry safety lives in Notion through `Last Adjustment ID`, while operational findings and
confirmed adjustment events live in the Financial Rules database.

## Mutation outcomes

Every completed run exposes one `mutation_outcome`:

| Disposition | Meaning |
| --- | --- |
| `not_needed` | The deterministic plan contains no Budget DB changes |
| `disabled` | A change is available, but the Active authorization rule is not `true` |
| `applied` | The guarded writer changed and postflight-verified the pages |
| `already_applied` | The exact idempotent operation was confirmed on retry |
| `blocked` | Freshness or preference verification rejected the proposal before writing |
| `failed_rolled_back` | A write failed and all completed page changes were restored |
| `partial_failure` | A write failed and rollback could not confirm full restoration |

Expected finance safety failures become visible outcomes in the trace. Unexpected integration
or programming errors still fail the graph so they cannot be mistaken for a successful run.

## Applied-adjustment context

Only `applied` and `already_applied` outcomes create applied-adjustment context. One compact
entry is stored per changed subcategory, including donor reductions. Its `current_signal`
contains the Budget DB operation ID and its amount is the signed change in ILS.

Applied adjustments are point-in-time events, not conditions. Daily reconciliation therefore
does not resolve them simply because a later report does not reproduce the event. They remain
available for later conversation presentation and month-retention handling.

## Manual LangSmith Studio run

Restart the local Agent Server after pulling this layer:

```bash
.venv/bin/langgraph dev
```

Select `daily_budget_monitor` and submit:

```json
{"as_of": "2026-08-27"}
```

Use the intended local date. The graph reads and may write live Notion data. Automatic changes
remain disabled unless this Active Financial Rule exists:

```text
Key: monitoring.automatic_budget_adjustments_enabled
Statement: true
```

For a safe first inspection, leave the rule absent or set to JSON `false`. The graph will still
calculate and reconcile observations, but its outcome will be `disabled` before the mutation
boundary.

## Still separate

- The Asia/Jerusalem 4:00 AM trigger
- Loading presentable operational context into the conversation graph
- Acknowledging a signal only after the assistant actually presents it
- External alert delivery and special handling for `partial_failure`
- Conversation-requested budget edits through draft + human approval
- Old-month operational-context retention
