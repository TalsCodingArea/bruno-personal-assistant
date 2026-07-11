# 🤖 Personal Assistant

A self-hosted AI-powered personal assistant running on a Raspberry Pi, built with LangChain and connected to Telegram. It manages finances, tracks movies, handles job applications, processes receipts, and runs daily automations — all through a simple chat interface.

---

## ✨ Features

### 💸 Finance Tracking
- Log expenses and income to Notion automatically
- Process receipts via PDF upload — extracts vendor, date, amount, and category using GPT-4o (with OCR fallback for scanned receipts)
- Monthly and weekly spending summaries
- Real-time budget evaluation after every logged expense

### 🏷️ ML Expense Categorization (human-in-the-loop)
- An on-device scikit-learn model (TF-IDF word + char n-grams → logistic regression, Hebrew-friendly) predicts Category / Sub Category for every new uncategorized Tal expense
- Predictions queue locally in `budget_data/ml/`; a batched Telegram digest every morning lists what's waiting
- Review through the assistant: confirm or correct each suggestion — confirmations update the Notion expense page and retrain the model on the spot
- Initial training happens automatically on first boot (all categorized `Tal 👨🏻` expenses are pulled from Notion); rerun manually anytime with `python scripts/train_expense_categorizer.py` to refresh the base dataset

### 🎬 Movie Tracker
- Add movies to a Notion watchlist with genres and AI-generated mood tags
- Log watches and ratings
- Get AI-powered movie suggestions based on your mood

### 💼 Job Applications
- Send a job URL → get a tailored resume, cover letter PDF, and personal note — all generated automatically
- Company research via DuckDuckGo + LLM synthesis
- Logs every application to a Notion jobs database

### 🧾 Receipt Processing
- Send a receipt PDF to the Telegram receipts channel
- Auto-extracts and categorizes the data, uploads to Notion, and evaluates the spend

### ⚙️ Automations
- Send JSON messages to the automations chat: `{"tool": "tool_name", "args": {}}`
- `log_expense` — Create a Notion expense from property-name args such as `Description`, `Amount`, `Date`, `Category`, `Sub Category`, `Payment Method`, and `Type`
- `morning_summary` — Daily performance recap based on your Notion day scores and workout streaks
- `get_weekly_spending_summary` — Weekly finance overview
- `evaluate_expense` — Inline budget check after each new expense

---

## 🏗️ Architecture

```
Telegram Bot (app.py)
    │
    └── personal_assistant/telegram
            ├── routing.py → routes updates by Telegram channel
            └── handlers/
                    ├── personal.py → main chat UX + streamed agent events
                    ├── receipts.py → PDF expense logging
                    ├── automations.py → structured JSON automation messages
                    └── jobs.py → job application pipeline delivery

Core domains:
    ├── Conversational Agent (LangGraph)
    │       ├── Tools: Notion CRUD, receipt OCR, movie search, ideas
    │       ├── Events: processing, tool_calling, generating_response, response_delta, done
    │       └── Memory: per-session chat history keyed by chat_id
    ├── Financial Advisor Capability (LangGraph)
    │       └── finance routing, affordability checks, budget tools, and saving plans
    └── Job Application Pipeline
            scrape → parse → research → generate docs → log to Notion
```

**Stack:** Python 3.13 · LangChain · OpenAI GPT-4o / GPT-4o-mini · Notion API · Telegram Bot API · WeasyPrint · BeautifulSoup

---

## 📁 Project Structure

```
├── app.py                        # Thin Telegram bot entry point
├── personal_assistant/
│   ├── config.py                 # Environment-backed settings and channel IDs
│   ├── runtime.py                # Shared LLM, memory, financial graph, session maps
│   └── telegram/
│       ├── bot.py                # Application creation and handler registration
│       ├── routing.py            # Channel-based Telegram routing
│       ├── formatting.py         # Telegram MarkdownV2 formatting helpers
│       ├── logging.py            # Best-effort logs-channel sender
│       └── handlers/             # Main, receipts, automations, and job delivery handlers
├── agent/
│   ├── builder.py                # LangGraph general assistant setup
│   ├── workflow.py               # Platform-neutral streamed agent events
│   ├── contexts/                 # Per-intent system prompts
│   └── llm.py                    # LLM configuration
├── tools/
│   ├── notion_tools.py           # Notion DB CRUD
│   ├── receipt_tools.py          # PDF OCR pipeline
│   ├── job_tools.py              # Job application workflow
│   ├── movie_tools.py            # Movie search & logging
│   └── registry.py              # Tool registration
├── router/
│   └── intent_router.py          # Message intent classifier
├── automation_functions.py       # Scheduled/triggered automations
├── base_scripts.py               # Shared utilities (email, Notion, OpenAI)
├── resume_data/
│   ├── resume_template.html      # Jinja2 resume template
│   └── cover_letter_template.html
└── docker-compose.yml
```

---

## 🚀 Setup

### 1. Clone & install dependencies

```bash
git clone https://github.com/TalsCodingArea/personal-assistant.git
cd personal-assistant
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy and fill in your `.env` file:

```env
# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID_PERSONAL_ASSISTANT=
TELEGRAM_CHAT_ID_RECEIPTS=
TELEGRAM_CHAT_ID_LOGS=
TELEGRAM_CHAT_ID_AUTOMATIONS=

# OpenAI
OPENAI_API_KEY=
ASSISTANT_LLM_MODEL=gpt-4o-mini
ASSISTANT_LLM_TEMPERATURE=0.7

# Notion
NOTION_API_KEY=  # also used to auth the read-only Notion MCP fallback (see below)
EXPENSES_DATABASE_ID=
INCOME_DATABASE_ID=
MOVIES_DATABASE_ID=
JOBS_DATABASE_ID=
# Notion — Automations
DAY_RATING_DATABASE_ID=
WORKOUTS_DATABASE_ID=

# Email (Gmail SMTP)
GMAIL_EMAIL=
GMAIL_APP_PASSWORD=
THINGS_EMAIL=

# Other
OMDB_API_KEY=
PDF_ENDPOINT_ACCESS_TOKEN=
RECEIPT_CATEGORY_OPTIONS=Groceries,Restaurant,Bills,EV,Online Services,Therapy,Decor

# Expense review digest (optional — defaults shown)
EXPENSE_REVIEW_DIGEST_HOUR=8
ASSISTANT_TIMEZONE=Asia/Jerusalem

# Calendar (structure only for now, see personal_assistant/integrations/calendar)
CALENDAR_PROVIDER=google
```

### 3. Prepare personal data files

These files are gitignored — you must create them locally:

| File | Description |
|---|---|
| `resume_data/user_profile.json` | Your personal info, experience, skills |
| `personal_notes_examples/*.txt` | Writing samples for few-shot note generation |
| `notion_config/databases.json` | Notion DB IDs map |
| `notion_config/finance_rules.json` | Budget % targets |

### 4. Run

```bash
python app.py
```

Or with Docker:
```bash
docker-compose up
```

---

## 📬 Telegram Channels

| Channel | Purpose |
|---|---|
| `personal_assistant` | General chat, finance, movies, job applications |
| `receipts` | Drop a receipt PDF here to auto-log it |
| `automations` | Send JSON like `{"tool": "morning_summary", "args": {}}` |
| `logs` | System output and confirmations |

---

## 🗺️ Roadmap

- [ ] **Calendar read access** — Query upcoming events from Google Calendar (provider-agnostic structure in place at `personal_assistant/integrations/calendar/`; Google auth + API calls pending)
- [ ] **Academic tasks integration** — Pull tasks and deadlines from academic sources
- [ ] **Smart study scheduler** — Analyze academic tasks and auto-book "Study Session" slots in the calendar based on priority and available time

---

## 🔌 Notion MCP fallback

For requests that don't fit any dedicated Notion tool, the agent has a
read-only fallback backed by Notion's official local MCP server
(`@notionhq/notion-mcp-server`, pinned to the version in
`notion_mcp.py` so upstream tool renames can't silently change the tool set),
spawned on demand via `npx` and authenticated with the same `NOTION_API_KEY`
above. Writes always go through the dedicated
tools in `tools/notion_tools.py` / `tools/financial_advisor/notion_tools.py` —
the MCP tools are filtered down to a read-only allowlist in
`personal_assistant/tools/mcp/notion_mcp.py`.

Requires Node.js (for `npx`) on whatever machine runs the bot. After first
install, verify the allowlist still matches the server's actual tool names:

```bash
python -m personal_assistant.tools.mcp.notion_mcp
```

If the Notion MCP server is unreachable, the fallback disables itself for a
5-minute cooldown and then retries — the rest of the agent keeps working
either way. An agent built during an outage picks the fallback tools back up
automatically once the server recovers.

---

## 🛠️ Host System Dependencies (Mac Mini)

The bot runs on a Mac Mini. System-level requirements:

```bash
# WeasyPrint (PDF generation)
brew install pango gdk-pixbuf libffi

# Node.js for the Notion MCP fallback (`npx` must be on PATH)
brew install node
```

<details>
<summary>Legacy: Raspberry Pi (previous host)</summary>

```bash
sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libpangocairo-1.0-0 \
                 libgdk-pixbuf2.0-0 libffi-dev shared-mime-info
```

</details>
