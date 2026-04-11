from base_scripts import *
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import json
from pathlib import Path
load_dotenv()

_BUDGET_DATA_DIR = Path(__file__).parent / "budget_data"

GMAIL_SMTP_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
notion_client = Client(auth=os.environ["NOTION_API_KEY"])
openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

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
        lines.append("Send 'start_budget_review' to the personal assistant to adjust.")
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


properties = {
    "Description": {
        "type": "title",
        "content": "Coffee"
    },
    "Amount": {
        "type": "number",
        "content": 3.5
    },
    "Category": {
        "type": "multi_select",
        "content": ["Food & Drink"]
    },
    "Sub Category":{
        "type": "multi_select",
        "content": ["Bills 🧾"]
    },
    "Date": {
        "type": "date",
        "content": datetime.now().strftime("%Y-%m-%d")
    },
    "Tag": {
        "type": "multi_select",
        "content": ["Tal 👨🏻"]
    }
}
# print(log_expense(properties))