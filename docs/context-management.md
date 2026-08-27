# Context management walkthrough

## What the node does

Every new user turn begins at `manage_context` in `app/graphs/conversation.py`.

1. It queries compact Active durable entries from the Financial Rules data source, excluding
   system-maintained `Operational Context` rows.
2. It separates finance entries from versioned `assistant.*` interaction settings.
3. It compiles defaults plus valid overrides into the effective interaction profile.
4. It replaces the graph state's cached profile snapshots with those fresh results.
5. It counts the approximate tokens in messages plus the previous summary.
6. If the threshold is reached, it summarizes only complete older turns.
7. It emits `RemoveMessage` entries for the summarized messages.
8. It keeps the most recent six user turns and their assistant/tool messages unchanged.

The assistant then receives two system messages: architectural policy, followed by the running
conversation summary, compact Active finance profile, and effective interaction profile.
Financial transactions and calculations are not copied into them; tools fetch those when needed.
Operational monitoring state is also excluded from normal prompt context. When the user
questions an event-driven alert, the assistant can call `get_expense_monitoring_decisions` to
load exact expense, budget, rule, calculation-version, and severity provenance on demand.
Daily operational signals still await their dedicated presentation/acknowledgement integration.

The architectural system prompt also contains the stable budget-management heuristic: prefer
prior-month stability, derive missing caps only from grounded budgets/income, preserve income
minus Budget pages as the variable pool, and account for future expenses over roughly three
months. Tal-specific budgeting exceptions remain versioned Financial Rules rather than being
added to the prompt.

## Cleanup settings

`ContextPolicy` owns deterministic defaults:

```python
ContextPolicy(
    compact_after_tokens=12_000,
    keep_recent_user_turns=6,
    max_profile_entries=100,
    max_profile_characters=12_000,
)
```

Removing messages compacts current state. It does not erase older checkpoint snapshots. A
production PostgreSQL checkpointer will therefore need a separate time- or count-based
retention job.

## Profile write sequence

```text
get_financial_profile
  → draft_financial_profile_update (no side effect)
  → apply_financial_profile_update
  → graph interrupt (Notion still unchanged)
  → resume same thread with approve/reject
  → create new Active page when approved
  → mark predecessor Superseded
```

Interaction settings follow the same safety sequence through
`draft_interaction_preference_update` and `apply_interaction_preference_update`. They use
controlled `assistant.*` keys rather than accepting arbitrary profile structure.

Use `start_conversation_turn` when a turn may request a write. If its result has
`requires_approval=True`, display `approval_request` to the user. Call `resume_approval` with
the same `thread_id` and an explicit boolean decision. `resume_profile_write` remains as a
compatibility wrapper.

## Manual inspection order

1. Inspect `.env.example` and `Settings.require_notion_data_source_ids()` to see configuration.
2. Inspect `FinancialProfileEntry` and `FinancialProfileUpdateDraft` in `domain/profile.py`.
3. Inspect `NotionFinancialProfileRepository` to see exact Notion payloads.
4. Inspect `FinancialProfileService` to see version and conflict rules.
5. Inspect the three profile tool files under `tools/read`, `tools/draft`, and `tools/write`.
6. Finally inspect `manage_context` and the graph edges in `graphs/conversation.py`.

This follows one request from the outer graph down through tools, services, and integrations,
without mixing Notion response dictionaries into calculation or conversation code.

## Studio approval exercise

Start the local server with `.venv/bin/langgraph dev`, open the emitted Studio URL, and select
`finance_agent`. A useful first prompt is:

```text
Remember that I want a minimum ₪2,000 monthly emergency buffer.
Draft the profile change and then ask me to approve it.
```

Inspect the `manage_context`, `assistant`, and tool traces. The apply tool's interrupt payload
contains the complete proposed Notion version. Before resuming, confirm the database is
unchanged. Resume with `{"action":"approve"}` to execute, or `{"action":"reject"}` to return a
rejection ToolMessage without writing.
