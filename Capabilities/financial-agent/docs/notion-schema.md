# Notion finance schema

This document records the current contract between the finance domain and Notion. All IDs are
loaded from `.env`; no source ID is kept in Python or documentation. Property names are
case-sensitive.

## Expenses

- Data source setting: `FINANCE_AGENT_EXPENSES_DATA_SOURCE_ID`
- Mandatory scope: every query includes `Tag contains "Tal 👨🏻"`
- Agent-facing budget fields: `Description`, `Date`, `Category`, `Sub Category`, `Payment Method`, `Final`
- `Final` is the authoritative budget value (`Amount × Actual`)
- Account settlement reads raw `Amount` for credit payments and `Mutual Formula` for rows tagged
  `Mutual 👫🏻`; expected reimbursement is `sum(Mutual Formula) / 2`
- Gift-card purchases remain excluded from budgets because their authoritative `Final` is zero

Current type assumptions to verify against the live schema:

- `Description`: title
- `Date`: date
- `Category`, `Sub Category`: multi-select
- `Payment Method`, `Type`: select
- `Tag`: multi-select
- `Amount`, `Actual`: number (`Actual` uses Notion's decimal percentage form)
- `Final`: numeric formula

The integration preserves every category and sub-category selection. A transaction receives a
canonical value only when exactly one option is selected. Multiple values are kept on the
domain object and flagged for review; calculations never silently select the first value or
count the full transaction against multiple budgets.

## Income

- Data source setting: `FINANCE_AGENT_INCOME_DATA_SOURCE_ID`
- Fields: `Name` (title), `Amount` (number), `Date` (date)
- Income is currently used when a forecast omits its explicit expected-income input

## Budget

- Data source setting: `FINANCE_AGENT_BUDGETS_DATA_SOURCE_ID`
- Fields:
  - `Name` (title)
  - `Date` (date)
  - `Budget` (number)
  - `Progressive` (select: `Accumulated` or `Discrete`)
  - `Volatility` (number formatted as a percentage)
  - `Baseline Budget` (number; empty until the first automatic adjustment)
  - `Last Adjustment ID` (rich text)
  - `Last Adjustment Reason` (rich text)
  - `Last Adjustment At` (date)
- `Name` must exactly equal the related expense `Sub-category`
- An expense is **regular** when its exact sub-category has a budget row for that month
- An expense is **variable** when its sub-category has no budget row for that month
- `Accumulated` means spending builds through the month and is suitable for daily-pace
  forecasting
- `Discrete` means a one-time expense; forecasting reserves the full budget instead of
  extrapolating its payment date across the month
- `Volatility` is the share of the remaining budget that can realistically be reduced. The
  Notion API represents 80% as `0.8`; the domain converts it to `80.00`
- Missing volatility is treated as unknown, never as adjustable
- Missing `Progressive` is conservatively forecast as discrete and surfaced as an assumption
- The guarded mutation adapter validates all four adjustment properties and the original five
  budget properties before writing; see `docs/budget-mutation.md`
- Validated monthly creation plans also use the four adjustment/audit properties. Their
  deterministic operation ID begins with `BCRT-`, Baseline Budget equals the initial Budget,
  and the reason identifies the cap basis and validated plan.

## Future Expenses

- Data source setting: `FINANCE_AGENT_FUTURE_EXPENSES_DATA_SOURCE_ID`
- Business rule: save toward a future expense for no more than three months in advance
- Current status: deterministic allocation and draft logic are ready, but Notion reads are
  blocked until the property mapping is supplied

The exact title, target amount, due date, saved amount/status, and any other properties still
need to be supplied. Until then, the agent cannot safely calculate a savings plan or write a
future expense without guessing the schema.

## Financial Rules / Context

- Data source setting: `FINANCE_AGENT_FINANCIAL_RULES_DATA_SOURCE_ID`
- Properties: `Name` (title), `Key` (rich text), `Kind` (select), `Scope` (multi-select),
  `Statement` (rich text), `Status` (status), `Supersedes` (one-way self relation), and
  `Operation ID` (Notion unique ID)
- `Kind`: `Preference`, `Decision`, `Constraint`, or `Goal`
- `Status`: `Active`, `Superseded`, or `Archived`
- Normal graph context loads only Active entries and only compact properties
- An approved update creates a new Active page linked to its immediate predecessor, then marks
  that predecessor Superseded
- Automatic `last_edited_time` is used to reject a draft if its source version changed
- Interaction settings reuse this source as `assistant.*` keys with `Scope=Conversation`; see
  `docs/interaction-profile.md` for their controlled values
- Daily monitoring recognizes controlled keys whose `Statement` is strict JSON:
  `monitoring.projection_bands` (number array), `monitoring.protected_subcategories` (string
  array), and `monitoring.preferred_donor_subcategories` (string array)
- `monitoring.automatic_budget_adjustments_enabled` is JSON boolean and defaults to `false`;
  unattended budget writes are impossible until its Active value is `true`
- Event monitoring additionally recognizes strict JSON values for
  `monitoring.alert.minimum_material_amount`, `monitoring.alert.cooldown_hours`,
  `monitoring.alert.material_projection_increase`, `monitoring.reallocation.enabled`,
  `monitoring.reallocation.max_amount`, and `monitoring.emergency_buffer_amount`
- Account planning recognizes `cashflow.minimum_available_balance` and
  `cashflow.savings_sweep_surplus`; each Statement is one non-negative JSON number. The first is
  the required post-settlement balance, and the second is the surplus-above-minimum threshold
  that triggers a savings-transfer recommendation.
- Missing monitoring keys use safe application defaults; malformed or duplicated Active
  monitoring keys stop the read rather than silently replacing explicit policy with defaults
- Notion's unique ID identifies a row; it does not by itself prevent two logically duplicate
  pages. The service additionally treats an identical Active `Key` + `Statement` as already
  current and refuses multiple differing Active versions.

### Operational context rows

Daily monitoring uses the same data source with no additional properties. System-maintained
rows use the reserved `monitoring.runtime.*` key prefix and the `Operational Context` plus
`Budget Monitoring` scopes. Their Statement is compact schema-versioned JSON and their page
body contains a readable summary. See `docs/operational-context.md` for identity, lifecycle,
acknowledgement, and versioning rules.

Durable profile queries explicitly exclude the `Operational Context` scope. Runtime rows are
therefore not returned by profile tools or inserted into the assistant prompt as if they were
user preferences. The later conversation-integration layer will load only presentable runtime
state through its dedicated repository.

## Bank Movement

- Configuration reuses `BANK_ACCOUNT_NOTION_DATABASE_ID` and optional
  `BANK_ACCOUNT_NOTION_DATA_SOURCE_ID` from the importer.
- Read fields are `Title`, `Date`, `Select`, `Amount`, `Balance`, `Description`, and `Action`.
- `get_bank_movements` is bounded to 100 rows. `get_account_outlook` reads only the most recent
  movement on or before its `as_of` date and uses that row's `Balance`.
- The finance capability exposes no Bank Movement create, update, or delete operation.

## Current write policy

Draft tools never call a Notion mutation endpoint. Financial-profile, interaction-profile, and
monthly-budget apply tools call LangGraph `interrupt()` before invoking their services. A client
must resume the same `thread_id` with explicit approval before Notion is changed.
