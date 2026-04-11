# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Identity — ALWAYS commit as:
```bash
git config user.name "TalsCodingArea"
git config user.email "Tals.Busi@gmail.com"
```
Set this at the start of EVERY session before any commit. Never include "Co-Authored-By", "Generated with Claude", or any AI attribution in commit messages.

## Project constraints
- Language: Python 3.13, venv at `.venv/`
- NEVER create files unless absolutely necessary — prefer editing existing ones
- Keep files under 500 lines
- NEVER commit `.env` files or secrets
- NEVER add `scraper/`, `frontend/`, or `backend/` directories

## Build / verify
```bash
pip install -r requirements.txt
python -c "import app"          # verify no import errors
```
No test suite exists. The import check is the only automated verification.

## Git workflow
```bash
git config user.name "TalsCodingArea"
git config user.email "Tals.Busi@gmail.com"
git add <specific files>
git commit -m "feat/fix/chore: descriptive message"
git push origin main
```

---

## Architecture overview

### Entry point — `app.py`
Telegram bot running in polling mode. Owns four chat channels (keyed by env vars):
- `personal_assistant` — main conversational agent
- `receipts` — PDF receipt OCR pipeline
- `automations` — zero-arg automation functions triggered by name
- `logs` — outbound logging only

App-level singletons (created once at startup): `llm`, `memory`, `budget_graph`, `budget_review_graph`.

Per-chat state dicts (keyed by `chat_id` string): `_agents`, `_budget_sessions`, `_budget_review_sessions`, `_pending_jobs`.

### Message routing in `personal_assistant` channel
`app.py:_handle_personal_assistant_text` checks in order:
1. Active budget review session → drive `budget_review_graph`
2. Active budget planning session → drive `budget_graph`
3. Cancel intent → clear sessions
4. Fast-path job URL regex → `_handle_job_application`
5. LLM intent classification (`router/intent_router.py`) → `budget`, `job_application`, or fall through to agent

### Conversational agent
`agent/builder.py` assembles: `create_tool_calling_agent` + `AgentExecutor` + `RunnableWithMessageHistory`.

- LLM: configured via `ASSISTANT_LLM_MODEL` env var (default `gpt-4o-mini`), built in `agent/llm.py`
- Memory: `MemoryStore` in `agent/memory.py` — `InMemoryChatMessageHistory` keyed by `chat_id`, lost on restart
- System prompt: `agent/system_prompt.py` — single `SYSTEM_PROMPT` string with MarkdownV2 rules, personality, and per-domain instructions
- Tools: `tools/registry.py:get_tools()` (static) + `get_workflow_tools()` (per-session factory, injected in `app.py:_get_or_build_agent`)

The import `from langchain_classic.agents import AgentExecutor, create_tool_calling_agent` is intentional — do not change this to `langchain`.

### Tools (`tools/`)
- `notion_tools.py` — all Notion DB I/O. Key function: `get_expenses_between_dates` returns `{period, total, count, by_category, by_subcategory, records}`. **The agent must use pre-aggregated fields (`total`, `by_category`, `by_subcategory`) and never re-sum `records`.** Also exposes `get_spending_habits`, `get_financial_advisor_habits`, `update_financial_advisor_habit`.
- `budget_tools.py` — pure-Python budget math: smart projections, insights, savings opportunities, Notion Budget DB read/write. `compute_smart_projections` accepts optional `actual_by_subcategory` + `habits_by_subcategory` for cadence-aware fixed-cost detection.
- `workflow_tools.py` — factory functions that create session-bound `start_budget_planning`, `start_budget_review`, and `apply_for_job` tools. These hold a reference to the app-level session state dicts.
- `registry.py` — two functions: `get_tools()` (static base tools) and `get_workflow_tools(chat_id, ...)` (per-session workflow tools). Must be kept in sync when adding new tools.
- `telegram_tools.py` — `markdown_v2_safe(text, preserve_formatting)` for safe MarkdownV2 output; `TelegramStatusCallback` AsyncCallbackHandler that edits a status message as tools fire.
- `receipt_tools.py` — PDF receipt OCR using OpenAI `gpt-4o` (text + image fallback via PyMuPDF).
- `job_tools.py` — 5-step job application pipeline (scrape → parse → research → generate PDFs → Notion log).
- `israeli_market_tools.py` — TASE stock quotes and exchange rates.

### LangGraph workflows (`agent/budget_workflow.py`)
Two separate `StateGraph` instances, both compiled with `interrupt_before=["chat"]` so they pause after each bot message:

**Budget planning** (`BudgetState`): phases `budget_input → review → unexpected → carryover → summary → done`. Persists confirmed recurring categories to `budget_data/repeating_categories.json`.

**Budget review** (`BudgetReviewState`): two nodes — `_review_analyze_node` (fetches budget + actuals + income, runs `compute_smart_projections`, builds message) and `_review_chat_node` (parses user approval, writes changes to Notion via `update_budget_categories`). Triggered by `start_budget_review` tool OR directly from `app.py:_handle_budget_review`.

Integration pattern: always use the `async_start_*` / `async_continue_*` helper functions — never call `graph.invoke` directly from app.py.

### Automation system (`automation_functions.py`)
Any public, zero-required-parameter function in this file is auto-discovered by `app.py:_load_automation_functions()` and callable by sending its exact name to the `automations` Telegram channel.

Key functions:
- `update_spending_habits()` — fetches last month's expenses, updates `budget_data/spending_habits.json` with rolling avg/min/max/last + cadence fields per subcategory. Run on 1st of each month.
- `backfill_spending_habits()` — rebuilds the habits file from scratch (Jan + Feb 2026 baseline).
- `review_budget()` — read-only budget snapshot for the automations channel.

### Financial habits data (`budget_data/`)
- `spending_habits.json` — rolling stats by category and subcategory. Subcategory entries include: `avg`, `min`, `max`, `last`, `avg_transactions`, `typical_day`, `months_present`, `parent_category`, `cadence` (one of: `monthly_once`, `biweekly`, `weekly`, `frequent`, `occasional`).
- `financial_advisor_habits.json` — advisor rules updated by the agent via `update_financial_advisor_habit`.
- `repeating_categories.json` — user-confirmed recurring budget categories from the planning workflow.

### Notion config (`notion_config/`)
- `databases.json` — logical name → database ID mapping (expenses, income, movies, jobs)
- `finance_rules.json` — Need/Want/Waste split percentages (kept for reference; agent no longer uses Need/Want/Waste breakdown)
- `properties/` — per-DB property schemas used by `NotionConfigLoader`
- `loader.py` — `NotionConfigLoader` class; must be importable from the project root (not from a subdirectory). When running scripts directly, `sys.path` must include the project root.

### Telegram formatting
All agent responses go through `tools/telegram_tools.py:markdown_v2_safe(text, preserve_formatting=True)`. Rules enforced everywhere:
- Never use `#`/`##`/`###` headings — they render as literal `#` in Telegram. Use `*bold*` instead.
- Never use `---` horizontal rules.
- Always escape MarkdownV2 special characters outside formatted spans.

### Required environment variables
```
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID_PERSONAL_ASSISTANT
TELEGRAM_CHAT_ID_RECEIPTS
TELEGRAM_CHAT_ID_LOGS
TELEGRAM_CHAT_ID_AUTOMATIONS
TELEGRAM_CHAT_ID_JOBS
NOTION_API_KEY
EXPENSES_DATABASE_ID
INCOME_DATABASE_ID
MOVIES_DATABASE_ID
JOBS_DATABASE_ID
BUDGET_DATABASE_ID
OPENAI_API_KEY
ASSISTANT_LLM_MODEL       # default: gpt-4o-mini
ASSISTANT_LLM_TEMPERATURE # optional
RECEIPT_CATEGORY_OPTIONS  # comma-separated list
```
