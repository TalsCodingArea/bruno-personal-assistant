# Agent-directed monthly budget planning

Budget creation belongs to the conversation graph. It is deliberately not a standalone graph
or a hardcoded category-allocation workflow: the model gathers evidence, applies the global
strategy and approved Financial Rules, and chooses the plan. Deterministic services validate
that proposal and guard persistence.

## Tool sequence

```text
get_budget_planning_context
  → draft_monthly_budget_plan
  → explain plan, stability changes, variable reserve, and warnings
  → apply_monthly_budget_plan only when Tal requests creation
  → LangGraph approval interrupt
  → fresh evidence and target-page verification
  → idempotent Notion Budget page creation
```

`get_budget_planning_context` returns:

- Budget pages already present for the target month;
- the previous month's Budget pages and total;
- recorded income for the target and previous months;
- Future Expenses due during the target month or following two months, with deterministic
  contribution amounts;
- approved Financial Rules scoped to Budgeting, Budget Planning, Savings, or Future Expenses;
- a source fingerprint that the draft/apply tools require.

The source fingerprint prevents the model from drafting against one set of evidence and later
applying after income, previous budgeting, future expenses, or relevant rules changed.

## Global planning strategy

The conversation graph's system prompt establishes these heuristics:

1. Prefer stable regular allocations. Start with prior-month subcategories and amounts, then
   change them only when the cap, income, future needs, or an approved rule supports the change.
2. A user-provided financial cap is the maximum total of the target month's Budget pages.
3. Without a user cap, select and explain a grounded cap from target/previous income and/or
   previous budgeting. Missing income is never invented.
4. `income assumption − all Budget pages` is the variable pool. Leave it non-negative and
   useful for impulse spending, additional savings, and occasional over-budget spending.
5. Known future expenses normally receive contributions over no more than three months.
6. Accumulated budgets are gradually consumed. Discrete budgets reserve one-time costs.
7. Volatility describes real donor flexibility: zero for protected commitments, higher only
   when unused allocation can safely move.

These are global model heuristics, not arithmetic hidden in a graph. Financial Rules remain the
place for Tal-specific exceptions and durable preferences.

## Draft validation

Each proposed page has an exact subcategory, amount, Progressive mode, Volatility percentage,
purpose (`regular` or `future_expense`), and rationale. The draft service rejects:

- duplicate proposed subcategories;
- a new page whose exact subcategory already exists for the target month;
- a total above the financial cap;
- a total above an explicitly selected income assumption;
- unsupported income or previous-budget cap evidence;
- silent underfunding of a known future-expense allocation.

The result reports existing, new, and final Budget totals; unused cap; remaining variable
reserve; required/planned future-expense funding; previous-month comparisons; warnings; and a
deterministic `BCRT-*` operation ID.

When Future Expenses still lacks its Notion property mapping, the context reports
`schema_not_configured` and the draft warns that future needs depend on user-provided context.
It does not fabricate an empty future-expense obligation.

## Approval and Notion creation

`apply_monthly_budget_plan` rebuilds the same draft, then interrupts before any write. Rejection
creates nothing. Approval triggers another full context read and verifies:

- planning sources still match the fingerprint;
- every target-month page that existed during drafting is unchanged;
- no conflicting page appeared for a requested subcategory;
- pages already carrying this plan's operation ID exactly match the approved plan.

New pages contain Name, Date, Budget, Progressive, Volatility, Baseline Budget, creation
operation ID/reason/time, using the existing Budget schema. The operation ID makes retries
idempotent. If Notion creates some pages before a later request fails, the error names confirmed
pages; retrying the same approved plan recognizes them and creates only the missing pages.

Notion has no multi-page transaction or conditional create. Concurrent external creation of
the same subcategory between final verification and the API call remains an integration-level
limitation; duplicate detection before the write fails closed for every observable conflict.
