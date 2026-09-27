# Guarded budget mutation boundary

Automatic budget mutation is internal infrastructure. It is not an agent tool and cannot be
called directly by ordinary conversation. The conversation can invoke the daily budget graph
through `check_expenses`; the same deterministic authorization, freshness checks, preference
review, postflight verification, and rollback path still owns every update. Validated creation
of missing monthly Budget pages is a separate autonomous tool path.

## Required Notion setup

Add these properties to the Budget data source before enabling automatic mutation:

| Property | Type | Purpose |
| --- | --- | --- |
| `Baseline Budget` | Number | Original monthly amount before the first automatic change |
| `Last Adjustment ID` | Rich text | Idempotency key for the most recent automatic operation |
| `Last Adjustment Reason` | Rich text | Human-readable before/after explanation |
| `Last Adjustment At` | Date | Timestamp of the confirmed write |

The repository validates the complete property schema before its first write. Missing or
incorrect types fail closed. Existing rows may leave all four values empty; the first write
sets Baseline Budget to the row's pre-adjustment Budget amount. Later writes preserve it.

Add this Active Financial Rule to authorize unattended changes:

```text
Key: monitoring.automatic_budget_adjustments_enabled
Statement: true
```

The Statement is JSON boolean `true`, not the text `"true"`. The default is `false`.

Human-authored rules that should participate in the GPT preference veto must include the exact
Scope `Budget Monitoring`. Controlled `monitoring.*` rules are enforced deterministically and
are not sent to the model as prose guidelines.

## Verification sequence

`BudgetMutationService.apply_report` uses this sequence:

```text
candidate DailyBudgetMonitoringReport
  → fresh Notion snapshot and deterministic recomputation
  → exact candidate/fresh fingerprint comparison
  → page-level mutation proposal
  → deterministic authorization, income, donor, and total checks
  → GPT veto-only review of every scoped human guideline
  → second fresh Notion snapshot and deterministic recomputation
  → exact proposal comparison
  → Notion schema validation and page preflight
  → sequential updates
  → postflight verification of every page
```

The model cannot alter amounts. It returns approval or blocking concerns and every guideline
key it considered. Omitting one relevant key blocks the mutation. When there are no additional
human-authored guidelines, the deterministic review is sufficient and no model call is made.

The page-level proposal includes the exact Budget page ID, current amount, Progressive,
Volatility, baseline and prior adjustment metadata, and automatic `last_edited_time`. Donor
reductions are rejected unless the page is Accumulated, has positive Volatility, and is not in
the protected-subcategory rules. The proposal must exactly match the deterministic plan, and
the resulting total cannot exceed logged current-month income.

## Idempotency and failure handling

The operation ID is a deterministic hash of the full report, including finance inputs, policy
versions, human guidelines, and calculated output. A retry that finds every page at the target
amount with the same Last Adjustment ID returns `already_applied` without writing again.

Notion does not provide a transaction spanning several pages or a conditional page-update
primitive. The adapter therefore:

1. Preflights every page before changing any page.
2. Writes pages sequentially.
3. Re-reads every page and verifies amount, baseline, and operation ID.
4. On failure, rolls back only pages still carrying this operation's exact ID and target value.

If every completed update is restored, the operation fails as `BudgetMutationApplyError`. If a
page changed again or rollback itself fails, it fails as `BudgetMutationPartialFailure`; the
daily graph exposes `partial_failure` and never records the mutation as applied. High-priority
external delivery remains part of the later alert integration.

Decimal is authoritative throughout the domain. At the final JSON boundary, Notion requires a
JSON number, so the adapter converts the already-cent-quantized Decimal and verifies the value
afterward.

## Preference-review model

The application uses the configured GPT-5.6 model through structured output. Its prompt is
explicitly veto-only and prohibits recalculation. The same deterministic proposal is rebuilt
after the model call, so a finance or rule edit during review invalidates the approval.

This follows OpenAI's structured-output pattern so the review result is typed rather than parsed
from prose. See the [official OpenAI structured outputs documentation](https://platform.openai.com/docs/guides/structured-outputs).

## Current boundary

Implemented:

- Proposal creation from deterministic plans
- Durable automatic-write authorization
- Scoped human-guideline collection
- GPT preference veto interface
- Two fresh recomputations
- Schema and page preflight
- Idempotent Notion writes
- Postflight verification and guarded rollback
- Invocation from the standalone daily LangGraph
- Applied-adjustment operational-context records

Not yet implemented:

- The 4:00 AM trigger
- High-priority partial-failure alert delivery
- Conversation-driven budget draft and approval tools
