# Bank account importer

This standalone module reads Israeli bank exports from a Mac folder, writes normalized bank
movements to Notion, and deletes an input file only after every transaction has either been
created or identified as an existing transaction.

## Notion schema

| Property | Type | Source |
| --- | --- | --- |
| `UID` | Text | `אסמכתא` plus transaction fingerprint; deterministic hash when blank |
| `Title` | Title | First available value from `פרטים`, `עבור`, `לטובת`, `הפעולה` |
| `Date` | Date | `תאריך` |
| `Select` | Select | `Positive` for `זכות`, `Negative` for `חובה` |
| `Amount` | Number | Absolute value of `זכות` or `חובה` |
| `Balance` | Number | `יתרה בש''ח` (the balance after this transaction) |
| `Description` | Text | Labeled combination of details, reference, value date, beneficiary, and purpose |
| `Action` | Text | `הפעולה` |

The reader uses only the first worksheet. It discovers the header within the first 30 rows, so
the current row 5 location and column order may change without code changes. A missing required
column or ambiguous row fails the whole file and leaves it in place.

## Debug one export

Install the project dependencies, then print the normalized rows without contacting Notion or
deleting the file:

```bash
.venv/bin/python -m bank_account debug /path/to/export.xlsx
```

To dry-run every file in the configured inbox:

```bash
.venv/bin/python -m bank_account run-once --dry-run
```

Launching `bank_account/__main__.py` with no arguments runs the complete live flow and reads the
folder directly from `BANK_ACCOUNT_INBOX_HOST`. It writes to Notion and deletes each source file
after every row has been created or safely identified as a duplicate. Use the VS Code profile
`Bank Importer: LIVE Full Flow (writes + deletes)` for this test. Keep only the file you intend
to process in the inbox.

## Configuration

Set `BANK_ACCOUNT_NOTION_DATABASE_ID` locally to the Bank Movement database ID. Set
`BANK_ACCOUNT_NOTION_DATA_SOURCE_ID` only if that database later contains more than one data
source. Neither ID belongs in source control. The importer deliberately uses the same
`FINANCE_AGENT_NOTION_TOKEN` as Bruno.

The Docker service mounts `BANK_ACCOUNT_INBOX_HOST` from the Mac at `/imports`. Use an absolute
Mac path, for example `/Users/your-name/Bank Imports`. The default is `./bank-inbox`, which also
keeps local dry runs pointed at the same folder.

Write paths literally in `.env`; do not add shell backslashes before spaces or `~` characters.
For example: `BANK_ACCOUNT_INBOX_HOST=/Users/your-name/Bank Imports`.

The trusted Telegram automation message `{"args":{},"tool":"new_bank_record"}` scans this same
folder immediately. The Bruno and daily-import containers share both the inbox and the SQLite
ledger, and a process lock prevents them from importing at the same time.

VS Code includes two read-only profiles—`Bank Importer: Debug Excel File` and `Bank Importer: Dry
Run Inbox`—plus `Bank Importer: LIVE Full Flow (writes + deletes)`. The live profile uses the
production import path while still supporting breakpoints. The debug entry point is
`bank_account/__main__.py`.

The SQLite ledger is persistent and records each transaction immediately after a successful
Notion write. A second remote check matches `UID` and the full visible transaction, so
overlapping exports are safe. Bank reference numbers are not assumed to be unique: the UID
combines `אסמכתא` with the row fingerprint. Any remaining UID conflict is reported while other
rows continue processing; the source file is kept for review. The daily service scans
immediately on startup and then every `BANK_ACCOUNT_SCAN_INTERVAL_SECONDS` (default: 86,400
seconds).
