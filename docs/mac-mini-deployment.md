# Mac Mini deployment

The Mac Mini keeps credentials and financial files locally. GitHub contains application code
and empty configuration placeholders only. Docker receives secrets at runtime through the
ignored `.env` file; they are not copied into the image.

## One-time setup

1. Install Git and Docker Desktop, start Docker Desktop, and configure it to start at login.
2. Clone the repository and enter it:

   ```bash
   git clone https://github.com/TalsCodingArea/bruno-personal-assistant.git
   cd bruno-personal-assistant
   ```

3. Create the local configuration and restrict its permissions:

   ```bash
   cp .env.example .env
   chmod 600 .env
   ```

4. Manually transfer the required values into `.env`. At minimum, review:

   - `OPENAI_API_KEY`
   - `FINANCE_AGENT_NOTION_TOKEN`
   - all `FINANCE_AGENT_*_DATA_SOURCE_ID` values
   - `BANK_ACCOUNT_NOTION_DATABASE_ID`
   - `BANK_ACCOUNT_NOTION_DATA_SOURCE_ID`, when the database has multiple data sources
   - `TELEGRAM_BOT_TOKEN`
   - all `TELEGRAM_CHAT_ID_*` values
   - `LANGSMITH_API_KEY`, when tracing is enabled
   - `BANK_ACCOUNT_INBOX_HOST`

5. Create the folder configured by `BANK_ACCOUNT_INBOX_HOST`. Do not place Excel exports in the
   repository itself unless that folder remains ignored.
6. Ensure the Notion integration is connected to every configured database.
7. Validate and start the stack:

   ```bash
   docker compose config --quiet
   docker compose up -d --build
   docker compose ps
   ```

The `bruno-data` Docker volume stores checkpoints, recurring tasks, deferred notices, and import
ledgers. Do not run
`docker compose down --volumes` unless deleting that state is intentional.

## Apply future GitHub updates

Run:

```bash
./scripts/update-mac-mini.sh
```

The updater refuses to overwrite tracked local edits, pulls `main` with `--ff-only`, refreshes
the base image, rebuilds the application, and recreates the services. The ignored `.env`, bank
Excel files, and `bruno-data` volume remain local.

Git is intentionally kept outside the Dockerfile. Putting repository credentials or `git pull`
inside the image would expose unnecessary access and would not provide a reliable container
update mechanism.
