from base_scripts import *
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
import os
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List
load_dotenv()

_BUDGET_DATA_DIR = Path(__file__).parent / "budget_data"

GMAIL_SMTP_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
notion_client = Client(auth=os.environ["NOTION_API_KEY"])
openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def generate_monthly_budgets(
    month: str = "",
    as_of: str = "",
    lookback_months: int = 6,
    dry_run: bool = True,
    include_non_predictable: bool = False,
) -> str:
    """
    Generate or preview monthly Budget database pages.

    Automation payload examples:
    Dry-run:
    {
      "tool": "generate_monthly_budgets",
      "args": {"month": "2026-05", "as_of": "2026-05-01", "lookback_months": 6}
    }

    Apply writes:
    {
      "tool": "generate_monthly_budgets",
        "args": {"month": "2026-05", "as_of": "2026-05-01", "lookback_months": 6, "dry_run": false}
    }
    """
    from tools.monthly_budget.models import Month
    from tools.monthly_budget.notion_writer import build_and_upsert_monthly_budget_pages

    target_month = Month.parse(month) if month else None
    as_of_date = date.fromisoformat(as_of) if as_of else None
    result = build_and_upsert_monthly_budget_pages(
        target_month=target_month,
        as_of=as_of_date,
        lookback_months=lookback_months,
        dry_run=dry_run,
        include_non_predictable=include_non_predictable,
    )

    preview = result["preview"]
    upsert = result["upsert"]
    totals = preview["totals"]
    writes = upsert["writes"]
    mode = "DRY RUN" if upsert["dry_run"] else "APPLIED"

    action_counts: Dict[str, int] = {}
    for write in writes:
        action = write["action"]
        action_counts[action] = action_counts.get(action, 0) + 1

    action_summary = ", ".join(f"{action}: {count}" for action, count in sorted(action_counts.items()))
    lines = [
        f"{mode} monthly Budget pages for {upsert['month']}",
        f"Financial Summary: {upsert['financial_summary_url']}",
        "",
        f"Predicted income: ₪{totals['predicted_income']:,.0f}",
        f"Budget pool: ₪{totals['spendable_budget']:,.0f}",
        f"Projected spend: ₪{totals['projected_spend']:,.0f}",
        f"Allocated budget: ₪{totals['allocated_budget']:,.0f}",
        f"Allocation gap: ₪{totals['allocation_gap']:,.0f}",
        f"Included categories: recurring + predictable_variable"
        + (" + non_predictable" if include_non_predictable else ""),
        "",
        f"Pages: {action_summary or 'none'}",
    ]

    for write in writes[:20]:
        lines.append(
            f"- {write['action']}: {write['sub_category']} | "
            f"₪{write['budget']:,.0f} | {write['date']}"
        )
    if len(writes) > 20:
        lines.append(f"...and {len(writes) - 20} more")

    if dry_run:
        lines.append("")
        lines.append("Dry-run only. Send the same payload with `dry_run: false` to write pages.")

    return "\n".join(lines)


def morning_summary():
    month_ago_date = datetime.now() - timedelta(days=90)
    filter_dict = {
        "and": [
            {
                "property": "Date",
                "date": {
                    "on_or_after": month_ago_date.strftime("%Y-%m-%d")
                }
            }
        ]
    }

    notion_client = Client(auth=os.environ["NOTION_API_KEY"])
    openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    last_90_days_data = get_notion_pages(notion_client, database_id=os.environ["DAY_RATING_DATABASE_ID"], filter=filter_dict)
    last_90_days_scores = [(entry['properties']["Day's Rating"]['formula']["number"], entry['properties']["Date"]["date"]["start"]) for entry in last_90_days_data]
    filter_dict = {
        "and": [
            {
                "property": "Date",
                "date": {
                    "on_or_after": month_ago_date.strftime("%Y-%m-%d")
                }
            },
            {
                "property": "Name",
                "title": {
                    "contains": "Workout"
                }
            }
        ]
    }
    last_90_days_workouts = get_notion_pages(notion_client, database_id=os.environ["PERSONAL_GROWTH_ENTRIES_DATABASE_ID"], filter=filter_dict)
    last_90_days_workouts = [entry['properties']['Date']['date']['start'] for entry in last_90_days_workouts]
    prompt = f"""Each day I log a day score that is affected by how many tasks I've managed to complete and my workout streaks.
    The number of tasks I completed is multiplied by the percent of tasks completed that day and it's added to the current workout streak count (if I worked out that day)
    Based on the following data from the last 90 days, provide a cheerful summary of my performance yesterday in comparison to the previous days, and highlight my current workout streak, so I can reflect on it this morning:
    Scores and Dates: {last_90_days_scores}
    Workout Dates: {last_90_days_workouts}
    """

    answer = ask_openai(prompt)
    return answer.replace("**", "*")


def get_weekly_spending_summary(category: str=""):

    """Fetches and summarizes weekly spending from a Notion database."""
    notion_client = Client(auth=os.environ["NOTION_API_KEY"])
    openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    last_sunday = datetime.now() - timedelta(days=datetime.now().weekday() + 1)
    filter_dict = {
        "and": [
            {
                "property": "Date",
                "date": {
                    "on_or_after": last_sunday.strftime("%Y-%m-%d")
                }
            }
        ]
    }
    if category:
        filter_dict["and"].append({
            "property": "Category",
            "select": {
                "equals": category
            }
        })

    expenses_data = get_notion_pages(notion_client, database_id="your_expenses_database_id", filter=filter_dict)
    expenses_list = [(entry['properties']['Amount']['number'], entry['properties']['Category']['select']['name'], entry['properties']['Date']['date']['start']) for entry in expenses_data]

    prompt = f"""Provide a summary of my spending over the last week based on the following data:
    Expenses (Amount, Category, Date): {expenses_list}
    """

    answer = ask_openai(prompt)
    return answer.replace("**", "*")


def evaluate_expense(last_expense: str):
    """Logs a new expense into the Notion database."""
    current_month_expenses = get_notion_pages(notion_client, database_id=os.environ["EXPENSES_DATABASE_ID"], filter={
        "and": [
            {
                "property": "Date",
                "date": {
                    "on_or_after": datetime.now().replace(day=1).strftime("%Y-%m-%d")
                }
            },
            {
                "property": "Tag",
                "multi_select": {
                    "contains": "Tal 👨🏻"
                }
            }
        ]
    })
    exclude_props = ["Yearly Finance Vacation 🏖️", "Yearly Finance Lifestyle 🏞️", "Yearly Finance Car 🚗",
                     "Invoice", "Academic Yearly Finance", "Budget", "Yearly Finance Spendings 📦",
                     "Yearly Finance Subscription ♻️", "Payment Method", "Shiri Budget", "Financial Analytics",
                     "Yearly Finance Home 🏡", "Tag"]
    clean_current_month_expenses = notion_response_simplifier(current_month_expenses, exclude=exclude_props)
    for entry in clean_current_month_expenses:
        if entry.get('Actual') and entry['Actual']:
            entry["Amount"] = entry["Amount"] * entry["Actual"]
    current_month_income = get_notion_pages(notion_client, database_id=os.environ["INCOME_DATABASE_ID"], filter={
        "and": [
            {
                "property": "Date",
                "date": {
                    "on_or_after": datetime.now().replace(day=1).strftime("%Y-%m-%d")
                }
            }
        ]
    })
    clean_current_month_income = notion_response_simplifier(current_month_income, exclude=exclude_props)
    total_income = sum(entry['Amount'] for entry in clean_current_month_income)
    bills_and_rent_past_data = get_notion_pages(notion_client, database_id=os.environ["EXPENSES_DATABASE_ID"], filter={
        "and": [
            {
                "property": "Date",
                "date": {
                    "before": datetime.now().replace(day=1).strftime("%Y-%m-%d")
                }
            },
            {
                "or": [
                    {
                        "property": "Sub Category",
                        "multi_select": {
                            "contains": "Bills 🧾"
                        }
                    },
                    {
                        "property": "Sub Category",
                        "multi_select": {
                            "contains": "Rent 💰"
                        }
                    }
                ]
            }
        ]
    })
    clean_bills_and_rent = notion_response_simplifier(bills_and_rent_past_data, exclude=exclude_props)
    expenses_goal = f"""
    I'm located in Israel so my currency is in ILS.
    My goal is to keep my "Need" type expenses under {total_income * 0.5} and my "Want" type expenses under {total_income * 0.3} each month.
    Bills & Rent this month should be predictable with the data from previous months.
    This is the 
    Based on the expenses so far this month: {clean_current_month_expenses}, provide me with a brief summary of how I'm doing towards my goals.
    This is the bills and rent data from previous months to help you understand my typical fixed costs: {clean_bills_and_rent}
    Your response should be concise and to the point. Make it with emojis and symbols so it will be engaging and easy to understand. No more than 3 sentences.
    This action is triggered when I log a new expense. So take into account that I've just logged an expense for {last_expense}.
    Locate if this new expense is "Want" or "Need" and reflect on that type of expense in your answer.
    Example responses (tailor them to the current situation): 
    Withing budget:
    "🟢 'Need' X / Y
    📈 At this pace: ~Z this month on "Needs"
    ✅ Looking good - spending is controlled, just keep an eye on Food & Drink ☕"

    Close to limit:
    "⚠️ Wants are getting tight — you’re at 85% of budget and we're only half way through the month. and relatively high spending in Category X.
    Food & Drink is the main driver. Consider slowing down this week 🍽️"

    Predictable breach:
    "🚨 At the current pace, Needs will hit 5,000 ILS this month
    — mainly due to consistent high spending in Category X.
    Consider adjusting your spending habits to stay within budget 📉"

    Over budget:
    "❌ This pushes Wants over budget.
    Current pace leads to 2,300 ILS this month — Food & Drink is the main leak 🚨"

    Stick to the examples structure but change it to be more engagins,  make it relevant to my current spending.
    """
    system_message = f"You are a personal finance assistant helping me track my expenses and stay within my budget."
    answer = ask_openai(expenses_goal, system_message=system_message)

    return answer.replace("**", "*")


def _derive_cadence(avg_transactions: float, months_present: int, total_months_tracked: int) -> str:
    """
    Classify a subcategory's spending cadence based on average transaction count.

    monthly_once  — single payment per month (Rent, Insurance, Electric)
    biweekly      — 2 payments per month
    weekly        — ~4 payments per month
    frequent      — more than once a week (daily coffee, etc.)
    occasional    — appears in fewer than half the months tracked
    """
    if total_months_tracked >= 2 and months_present / total_months_tracked < 0.5:
        return "occasional"
    if avg_transactions <= 1.5:
        return "monthly_once"
    if avg_transactions <= 3.0:
        return "biweekly"
    if avg_transactions <= 6.0:
        return "weekly"
    return "frequent"


def _apply_month_to_habits(habits: dict, data: dict, month_label: str) -> dict:
    """
    Merge one month of expense data (from get_expenses_between_dates) into a habits dict.
    Updates both by_category and by_subcategory with rolling avg / min / max / last.
    For subcategories also tracks: avg_transactions, typical_day, cadence, parent_category.
    Mutates and returns the habits dict.
    """
    months_tracked = habits.get("months_tracked", 0)

    # Build per-subcategory transaction stats from individual records
    sub_stats: dict = {}
    for record in data.get("records", []):
        date_str = record.get("date") or ""
        day = int(date_str[8:10]) if len(date_str) >= 10 else 0
        parent = ((record.get("category") or []) + [""])[0]
        for sub in (record.get("sub_category") or []):
            if not sub:
                continue
            if sub not in sub_stats:
                sub_stats[sub] = {"count": 0, "days": [], "parent_category": parent}
            sub_stats[sub]["count"] += 1
            if day:
                sub_stats[sub]["days"].append(day)

    for dimension in ("by_category", "by_subcategory"):
        existing = habits.setdefault(dimension, {})
        for key, amount in data.get(dimension, {}).items():
            prev = existing.get(key)

            if prev:
                new_avg = round(((prev["avg"] * months_tracked) + amount) / (months_tracked + 1), 2)
                entry = {
                    "avg": new_avg,
                    "min": round(min(prev["min"], amount), 2),
                    "max": round(max(prev["max"], amount), 2),
                    "last": round(amount, 2),
                }
            else:
                entry = {
                    "avg": round(amount, 2),
                    "min": round(amount, 2),
                    "max": round(amount, 2),
                    "last": round(amount, 2),
                }

            # Enrich subcategory entries with cadence fields
            if dimension == "by_subcategory":
                stats = sub_stats.get(key, {})
                new_txn_count = stats.get("count", 1)
                days = stats.get("days", [])
                new_typical_day = round(sum(days) / len(days)) if days else 0

                if prev:
                    prev_mp = prev.get("months_present", 1)
                    prev_avg_txn = prev.get("avg_transactions", float(new_txn_count))
                    prev_typical_day = prev.get("typical_day", new_typical_day)
                    new_avg_txn = round(((prev_avg_txn * prev_mp) + new_txn_count) / (prev_mp + 1), 2)
                    # Only average in a real typical_day when we have one
                    if new_typical_day:
                        new_avg_day = round(((prev_typical_day * prev_mp) + new_typical_day) / (prev_mp + 1))
                    else:
                        new_avg_day = prev_typical_day
                    months_present = prev_mp + 1
                    parent_cat = stats.get("parent_category") or prev.get("parent_category", "")
                else:
                    new_avg_txn = float(new_txn_count)
                    new_avg_day = new_typical_day
                    months_present = 1
                    parent_cat = stats.get("parent_category", "")

                entry["avg_transactions"] = new_avg_txn
                entry["typical_day"] = new_avg_day
                entry["months_present"] = months_present
                entry["parent_category"] = parent_cat
                entry["cadence"] = _derive_cadence(new_avg_txn, months_present, months_tracked + 1)

            existing[key] = entry

    habits["months_tracked"] = months_tracked + 1
    habits["last_updated"] = month_label
    return habits


def review_budget():
    """
    Fetch the current month's Notion budget, compare to actual expenses so far,
    and return a summary of deviations. Does NOT modify anything — read-only snapshot.
    Use this to get a quick budget health check from the automations channel.
    """
    from tools.budget_tools import fetch_current_month_budget
    from tools.notion_tools import get_expenses_between_dates, get_income_between_dates
    from datetime import date

    today = date.today()
    start = today.replace(day=1).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")

    try:
        budget_data = fetch_current_month_budget()
    except (ValueError, RuntimeError) as exc:
        return f"❌ Could not load budget: {exc}"

    budget = budget_data["categories"]
    month = budget_data["month"]

    try:
        expense_result = get_expenses_between_dates.invoke({"start_date": start, "end_date": end})
        actual = expense_result.get("by_category", {})
    except Exception:
        actual = {}

    try:
        income_rows = get_income_between_dates.invoke({"start_date": start, "end_date": end})
        income = sum(r.get("Amount") or 0 for r in income_rows if isinstance(r.get("Amount"), (int, float)))
    except Exception:
        income = 0.0

    total_budget = sum(budget.values())
    lines = [f"📊 Budget Snapshot — {month}\n",
             f"💰 Income: ₪{income:,.0f}  |  Budgeted: ₪{total_budget:,.0f}  |  To save: ₪{income - total_budget:,.0f}\n"]

    deviations = []
    for cat, b in sorted(budget.items(), key=lambda x: -x[1]):
        a = actual.get(cat, 0.0)
        if b == 0:
            continue
        pct = (a - b) / b * 100
        flag = " 🔴" if pct > 20 else (" 🟡" if pct > 10 else "")
        if flag or a > 0:
            lines.append(f"  • {cat}: ₪{b:,.0f} budget / ₪{a:,.0f} actual{flag}")
        if pct > 20:
            deviations.append(cat)

    if deviations:
        lines.append(f"\n⚠️ Over budget by >20%: {', '.join(deviations)}")
        lines.append("Ask the personal assistant to review monthly budget status and adjust the affected categories.")
    else:
        lines.append("\n✅ All categories within budget.")

    return "\n".join(lines)


def backfill_spending_habits():
    """
    One-time backfill: fetches January and February 2026 expenses and seeds
    budget_data/spending_habits.json with a 2-month baseline.
    Safe to run multiple times — always rebuilds from scratch.
    """
    from tools.notion_tools import get_expenses_between_dates

    months = [
        ("2026-01-01", "2026-01-31", "2026-01"),
        ("2026-02-01", "2026-02-28", "2026-02"),
    ]

    habits_path = _BUDGET_DATA_DIR / "spending_habits.json"
    habits_path.parent.mkdir(parents=True, exist_ok=True)
    habits = {"last_updated": None, "months_tracked": 0, "by_category": {}, "by_subcategory": {}}

    processed = []
    for start, end, label in months:
        data = get_expenses_between_dates.invoke({"start_date": start, "end_date": end})
        habits = _apply_month_to_habits(habits, data, label)
        processed.append(label)

    habits_path.write_text(json.dumps(habits, ensure_ascii=False, indent=2), encoding="utf-8")
    n_cats = len(habits["by_category"])
    n_subs = len(habits["by_subcategory"])
    return f"✅ Backfill complete — {', '.join(processed)}. {n_cats} categories, {n_subs} subcategories tracked."


def update_spending_habits():
    """
    Fetches last month's expenses from Notion and updates spending_habits.json
    with rolling averages for both categories and subcategories.
    Run automatically on the 1st of each month.
    """
    from tools.notion_tools import get_expenses_between_dates

    today = datetime.now()
    first_of_last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    last_of_last_month = today.replace(day=1) - timedelta(days=1)
    month_label = first_of_last_month.strftime("%Y-%m")

    data = get_expenses_between_dates.invoke({
        "start_date": first_of_last_month.strftime("%Y-%m-%d"),
        "end_date": last_of_last_month.strftime("%Y-%m-%d"),
    })

    habits_path = _BUDGET_DATA_DIR / "spending_habits.json"
    habits_path.parent.mkdir(parents=True, exist_ok=True)
    if habits_path.exists() and habits_path.read_text(encoding="utf-8").strip():
        habits = json.loads(habits_path.read_text(encoding="utf-8"))
    else:
        habits = {"last_updated": None, "months_tracked": 0, "by_category": {}, "by_subcategory": {}}

    habits = _apply_month_to_habits(habits, data, month_label)
    habits_path.write_text(json.dumps(habits, ensure_ascii=False, indent=2), encoding="utf-8")

    n_cats = len(habits["by_category"])
    n_subs = len(habits["by_subcategory"])
    return f"✅ Spending habits updated for {month_label} — {n_cats} categories, {n_subs} subcategories tracked."


EXPENSE_REQUIRED_PROPERTIES = ("Description", "Amount", "Date")

EXPENSE_PROPERTY_TYPES = {
    "Description": "title",
    "Amount": "number",
    "Actual": "number",
    "Date": "date",
    "Category": "multi_select",
    "Sub Category": "multi_select",
    "Tag": "multi_select",
    "Payment Method": "select",
    "Type": "select",
    "Invoice": "file",
}

EXPENSE_UNSUPPORTED_PROPERTIES = {
    "Academic Yearly Finance",
    "Final",
    "Financial Analytics",
    "Financial Summary",
    "Place",
    "Shiri Budget",
    "Yearly Finance Car 🚗",
    "Yearly Finance Home 🏡",
    "Yearly Finance Lifestyle 🏞️",
    "Yearly Finance Spendings 📦",
    "Yearly Finance Subscriptions ♻️",
    "Yearly Finance Vacation 🏖️",
}

EXPENSE_SELECT_OPTIONS = {
    "Category": [
        "Uncategorized",
        "Home 🏡",
        "Lifestyle 🏞️",
        "Car 🚗",
        "Spendings 📦",
        "Subscriptions ♻️",
        "Vacation 🏖️",
        "Academic 🎓",
        "Unrecognized",
    ],
    "Sub Category": [
        "Car Service ⚙️",
        "Car Wash 🧽",
        "Decor 🪑",
        "One Time Purchase 1️⃣",
        "Lunch 🍽️",
        "Insurance 🦺",
        "Reimburse 👈🏻",
        "Shop 🛖",
        "Bills 🧾",
        "Snacks & Drinks 🍫",
        "Parking 🅿️",
        "Super-Pharm 💊",
        "Fuel ⛽",
        "Groceries 🛒",
        "Other 🤷🏻‍♂️",
        "Home 🏡",
        "Clothing 👕",
        "Night Out 🍻",
        "Adventure ☀️",
        "Shiri 💌",
        "Electronics 📺",
        "Electric 🔋",
        "Restaurant 🍷",
        "Takeout 🥡",
        "Gift 🎁",
        "Mutual 🙏🏻",
        "In review",
        "Transport 🚌",
        "Barber 💈",
        "👨🏻‍💻 Personal Projects",
        "Media Services 📺",
        "Tuition 📚",
        "Therapy 🧘🏻‍♂️",
        "Gym",
        "Gym 🏋🏻",
        "🍿 Movies",
        "🏨 Accommondation",
        "🏂 Activities",
        "🫀 Health",
        "Date 🫶🏻",
        "Rent 💰",
        "Snacks & Drinks :chocolate_bar:",
        "Nails💅",
    ],
    "Tag": [
        "Shiri 👧🏻",
        "Tal 👨🏻",
        "Mutual 👫🏻",
    ],
    "Payment Method": [
        "Cibus",
        "Credit",
        "Bank",
        "Cash",
        "Gift Card",
    ],
    "Type": [
        "Waste",
        "Want",
        "Need",
        "wam",
    ],
}


def _format_options(options: Iterable[str]) -> str:
    return ", ".join(f"`{option}`" for option in options)


def _require_text(property_name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"`{property_name}` must be a non-empty string.")
    return value.strip()


def _coerce_number(property_name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"`{property_name}` must be a number.")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except ValueError as exc:
            raise ValueError(f"`{property_name}` must be a number.") from exc
    raise ValueError(f"`{property_name}` must be a number.")


def _validate_date_string(property_name: str, value: Any) -> str:
    date_value = _require_text(property_name, value)
    try:
        datetime.fromisoformat(date_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"`{property_name}` must be an ISO date or datetime string.") from exc
    return date_value


def _coerce_date(property_name: str, value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        return {"start": _validate_date_string(property_name, value)}
    if isinstance(value, dict):
        start = _validate_date_string(f"{property_name}.start", value.get("start"))
        date_value = {"start": start}
        if value.get("end") is not None:
            date_value["end"] = _validate_date_string(f"{property_name}.end", value.get("end"))
        if value.get("time_zone") is not None:
            date_value["time_zone"] = _require_text(f"{property_name}.time_zone", value.get("time_zone"))
        return date_value
    raise ValueError(f"`{property_name}` must be an ISO date string or an object with `start`.")


def _coerce_select(property_name: str, value: Any) -> str:
    selected = _require_text(property_name, value)
    options = EXPENSE_SELECT_OPTIONS[property_name]
    if selected not in options:
        raise ValueError(
            f"`{property_name}` must be one of: {_format_options(options)}."
        )
    return selected


def _coerce_multi_select(property_name: str, value: Any) -> List[str]:
    if isinstance(value, str):
        names = [value.strip()] if value.strip() else []
    elif isinstance(value, list):
        names = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"`{property_name}` must contain only non-empty strings.")
            names.append(item.strip())
    else:
        raise ValueError(f"`{property_name}` must be a string or a list of strings.")

    options = EXPENSE_SELECT_OPTIONS[property_name]
    invalid = [name for name in names if name not in options]
    if invalid:
        raise ValueError(
            f"`{property_name}` has unsupported option(s): {_format_options(invalid)}. "
            f"Allowed options: {_format_options(options)}."
        )
    return names


def _coerce_files(property_name: str, value: Any) -> List[Dict[str, Any]]:
    file_items = value if isinstance(value, list) else [value]
    files = []
    for item in file_items:
        if not isinstance(item, dict):
            raise ValueError(f"`{property_name}` files must be objects with `name` and `url`.")
        name = _require_text(f"{property_name}.name", item.get("name"))
        url = _require_text(f"{property_name}.url", item.get("url"))
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"`{property_name}.url` must start with http:// or https://.")
        files.append({"name": name, "type": "external", "external": {"url": url}})
    return files


def _build_expense_notion_properties(raw_properties: Dict[str, Any]) -> Dict[str, Any]:
    if not raw_properties:
        raise ValueError("`args` must include expense properties.")

    defaults = {"Tag": ["Tal 👨🏻"],
                "Payment Method": "Credit"}
    merged = {**defaults, **raw_properties}

    missing = [name for name in EXPENSE_REQUIRED_PROPERTIES if name not in merged]
    if missing:
        raise ValueError(f"Missing required expense properties: {_format_options(missing)}.")

    unsupported = [name for name in merged if name in EXPENSE_UNSUPPORTED_PROPERTIES]
    if unsupported:
        raise ValueError(
            f"These expense properties exist but are not supported by `log_expense` yet: "
            f"{_format_options(unsupported)}."
        )

    unknown = [name for name in merged if name not in EXPENSE_PROPERTY_TYPES]
    if unknown:
        known = sorted([*EXPENSE_PROPERTY_TYPES, *EXPENSE_UNSUPPORTED_PROPERTIES])
        raise ValueError(
            f"Unknown expense properties: {_format_options(unknown)}. "
            f"Known properties: {_format_options(known)}."
        )

    notion_properties: Dict[str, Any] = {}
    for property_name, value in merged.items():
        property_type = EXPENSE_PROPERTY_TYPES[property_name]
        if property_type == "title":
            notion_properties[property_name] = {
                "title": [{"type": "text", "text": {"content": _require_text(property_name, value)}}]
            }
        elif property_type == "number":
            notion_properties[property_name] = {"number": _coerce_number(property_name, value)}
        elif property_type == "date":
            notion_properties[property_name] = {"date": _coerce_date(property_name, value)}
        elif property_type == "select":
            notion_properties[property_name] = {"select": {"name": _coerce_select(property_name, value)}}
        elif property_type == "multi_select":
            notion_properties[property_name] = {
                "multi_select": [{"name": name} for name in _coerce_multi_select(property_name, value)]
            }
        elif property_type == "file":
            notion_properties[property_name] = {"files": _coerce_files(property_name, value)}
    return notion_properties


_NON_SHEKEL_CURRENCY_RE = re.compile(
    r"(\$|€|£|\bUSD\b|\bEUR\b|\bGBP\b|\bDOLLARS?\b|\bEUROS?\b)",
    re.IGNORECASE,
)
_SHEKEL_CURRENCY_RE = re.compile(r"(₪|\bILS\b|\bNIS\b|ש\s*\"?\s*ח|שקל)", re.IGNORECASE)
_AMOUNT_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _coerce_auto_expense_amount(amount: Any) -> float | None:
    """
    Return an ILS amount for auto expense logging.

    Numeric values are accepted as ILS. Strings must be explicitly shekel-denominated
    (for example: "₪75.00"). Foreign-currency strings are skipped because the final
    shekel charge can include unknown credit-card conversion and commission.
    """
    if isinstance(amount, (int, float)) and not isinstance(amount, bool):
        return float(amount)
    if not isinstance(amount, str):
        raise ValueError("`amount` must be a number or shekel amount string.")

    text = amount.strip()
    if not text:
        raise ValueError("`amount` must be a number or shekel amount string.")
    if _NON_SHEKEL_CURRENCY_RE.search(text):
        return None
    if not _SHEKEL_CURRENCY_RE.search(text):
        raise ValueError("String `amount` must include a shekel currency marker.")

    match = _AMOUNT_NUMBER_RE.search(text)
    if not match:
        raise ValueError("String `amount` must include a numeric amount.")
    return float(match.group(0).replace(",", ""))


def auto_expense_tool(description: str, amount: float | str):
    """
    Log an uncategorized credit-card expense with today's date.

    Automation payload example:
    {
      "tool": "auto_expense_tool",
      "args": {"description": "Coffee", "amount": 12.5}
    }
    """
    description_value = _require_text("description", description)
    amount_value = _coerce_auto_expense_amount(amount)
    if amount_value is None:
        return f"Skipped non-shekel expense: {description_value} — {amount}"
    if amount_value <= 0:
        raise ValueError("`amount` must be positive.")

    return log_expense(
        Description=description_value,
        Amount=amount_value,
        Date=datetime.now().isoformat(),
        Category=["Uncategorized"],
    )


def _strip_json_markdown(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        first = lines[0].strip().lower()
        if first in ("```", "```json"):
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _parse_expense_text_with_llm(text: str) -> Dict[str, Any]:
    message_text = _require_text("text", text)
    model = os.getenv("ASSISTANT_LLM_MODEL", "gpt-4o-mini")
    prompt = f"""Extract one credit-card transaction from this SMS.

Return only valid JSON with this exact shape:
{{"description": "<merchant name>", "amount": <number>}}

Rules:
- The description is the merchant name, not the card company.
- In Hebrew CAL messages, the merchant often appears after the card suffix and starts with "ב"; omit that leading "ב".
- Amount is in Israeli shekels when the text says "שח", "₪", "ILS", or similar.
- Do not infer category or subcategory.

SMS:
{message_text}
"""

    response = openai_client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": "You extract expense transaction data and return strict JSON only."},
            {"role": "user", "content": prompt},
        ],
    )
    content = response.choices[0].message.content or ""
    try:
        parsed = json.loads(_strip_json_markdown(content))
    except json.JSONDecodeError as exc:
        raise ValueError("LLM did not return valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object.")
    return parsed


def log_txt_expense(text: str):
    """
    Parse a credit-card SMS with an LLM and log it as an uncategorized expense.

    Automation payload example:
    {
      "tool": "log_txt_expense",
      "args": {"text": "היי, ... בסך 400 שח."}
    }
    """
    parsed = _parse_expense_text_with_llm(text)
    description = _require_text("description", parsed.get("description"))
    amount = _coerce_number("amount", parsed.get("amount"))
    if amount <= 0:
        raise ValueError("`amount` must be positive.")
    return auto_expense_tool(description=description, amount=amount)


def log_expense(**properties):
    """
    Create an expense row in the Notion expenses database.

    Automation payload example:
    {
      "tool": "log_expense",
      "args": {
        "Description": "Coffee",
        "Amount": 12.5,
        "Date": "2026-04-29",
        "Category": ["Lifestyle 🏞️"],
        "Sub Category": ["Snacks & Drinks 🍫"],
        "Payment Method": "Credit",
        "Type": "Want"
      }
    }
    """
    database_id = os.getenv("EXPENSES_DATABASE_ID")
    if not database_id:
        raise ValueError("Missing EXPENSES_DATABASE_ID environment variable.")
    notion_properties = _build_expense_notion_properties(properties)
    page = notion_client.pages.create(
        parent={"database_id": database_id},
        properties=notion_properties,
    )

    description = properties.get("Description", "Expense")
    amount = _coerce_number("Amount", properties.get("Amount"))
    url = page.get("url")
    message = f"✅ Logged expense: {description} — ₪{amount:g}"
    if url:
        message += f"\n{url}"
    try:
        from tools.monthly_budget.budget_monitor import evaluate_logged_expense_budget, format_budget_alerts

        alerts = evaluate_logged_expense_budget(properties)
        alert_text = format_budget_alerts(alerts)
        if alert_text:
            message += f"\n\n{alert_text}"
    except Exception as exc:
        message += f"\n\n⚠️ Budget evaluation failed: {exc}"
    return message
