"""
tools/budget_tools.py — Monthly budget analysis and planning utilities.

All functions here are pure Python / Notion queries — no LLM calls.
LLM usage is handled by the main assistant / financial capability graph.

Key functions:
  fetch_monthly_expenses(year, month)          → List of expense dicts
  analyze_spending_patterns(lookback_months)   → Per-sub-category stats + trends
  identify_repeating_categories(analysis)      → (repeating, suggested_new)
  compute_budget_breakdown(...)                → Final allocation dict
  format_analysis_message(...)                 → Human-readable analysis string
  format_breakdown_message(...)               → Human-readable breakdown string

Persistence (budget_data/repeating_categories.json):
  load_persisted_categories()                  → (confirmed: List[Dict], excluded: Set[str])
  save_persisted_categories(confirmed, excluded_names) → writes to JSON
  merge_categories_with_persisted(...)         → merges Notion-detected with saved prefs
"""

import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# A sub-category must appear in at least this many months to be "repeating"
_REPEATING_THRESHOLD = 2

# ≥ this % change between first and last month counts as a trend
_TREND_SIGNIFICANT_PCT = 0.15


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_monthly_expenses(year: int, month: int) -> List[Dict[str, Any]]:
    """
    Fetch all expenses for a given calendar month from Notion.

    Returns a list of dicts with keys:
      Description, Category (list), Sub Category (list), Date, Amount
    """
    from tools.notion_tools import get_expenses_between_dates

    start = date(year, month, 1).isoformat()
    if month == 12:
        end = (date(year + 1, 1, 1) - timedelta(days=1)).isoformat()
    else:
        end = (date(year, month + 1, 1) - timedelta(days=1)).isoformat()

    try:
        return get_expenses_between_dates.invoke({"start_date": start, "end_date": end})
    except Exception as exc:
        logger.warning("Failed to fetch expenses for %d-%02d: %s", year, month, exc)
        return []


def _get_sub_categories(expense: Dict[str, Any]) -> List[str]:
    """
    Extract sub-category names from an expense dict.
    Handles both list (multi_select) and plain string.
    Falls back to Category, then 'Uncategorized'.
    """
    raw = expense.get("Sub Category", "")
    if isinstance(raw, list) and raw:
        return [s.strip() for s in raw if s.strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]

    # Fallback to Category
    raw = expense.get("Category", "")
    if isinstance(raw, list) and raw:
        return [s.strip() for s in raw if s.strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]

    return ["Uncategorized"]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_spending_patterns(lookback_months: int = 3) -> Dict[str, Any]:
    """
    Fetch the last N complete calendar months and compute per-sub-category stats.

    Returns:
    {
      "months_analyzed": ["2026-01", "2026-02", "2026-03"],
      "by_category": {
        "Groceries 🛒": {
          "monthly_totals": [820.0, 750.0, 890.0],   # oldest → newest
          "months_present": 3,
          "avg": 820.0,
          "trend": "↑",  # "↑" | "↓" | "→"
        },
        ...
      }
    }
    """
    today = date.today()

    # Build list of (year, month) tuples, going back from the previous complete month
    months: List[Tuple[int, int]] = []
    for i in range(lookback_months, 0, -1):
        m = today.month - i
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        months.append((y, m))

    month_labels = [f"{y}-{m:02d}" for y, m in months]

    # Fetch expenses per month
    monthly_expenses: List[List[Dict]] = [fetch_monthly_expenses(y, m) for y, m in months]

    # Aggregate totals by sub-category per month
    by_category: Dict[str, Dict[str, Any]] = {}

    for month_idx, expenses in enumerate(monthly_expenses):
        month_totals: Dict[str, float] = {}
        for exp in expenses:
            amount = float(exp.get("Amount") or 0)
            for cat in _get_sub_categories(exp):
                month_totals[cat] = month_totals.get(cat, 0.0) + amount

        for cat, total in month_totals.items():
            if cat not in by_category:
                by_category[cat] = {"monthly_totals": [0.0] * lookback_months}
            by_category[cat]["monthly_totals"][month_idx] = total

    # Compute derived stats for each category
    for cat, data in by_category.items():
        totals = data["monthly_totals"]
        nonzero = [t for t in totals if t > 0]
        data["months_present"] = len(nonzero)
        data["avg"] = sum(nonzero) / len(nonzero) if nonzero else 0.0

        # Trend: compare earliest vs latest month with spending
        if len(nonzero) >= 2:
            first = next(t for t in totals if t > 0)
            last = next(t for t in reversed(totals) if t > 0)
            if first > 0:
                change = (last - first) / first
                if change >= _TREND_SIGNIFICANT_PCT:
                    data["trend"] = "↑"
                elif change <= -_TREND_SIGNIFICANT_PCT:
                    data["trend"] = "↓"
                else:
                    data["trend"] = "→"
            else:
                data["trend"] = "→"
        else:
            data["trend"] = "→"

    return {
        "months_analyzed": month_labels,
        "by_category": by_category,
    }


def identify_repeating_categories(
    analysis: Dict[str, Any],
    threshold_months: int = _REPEATING_THRESHOLD,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Split categories into repeating (appear in ≥ threshold months)
    and suggested_new (appeared in exactly threshold-1 months).

    Each item dict: {name, avg, months_present, trend, expected_amount}
    Returns: (repeating, suggested_new)
    """
    total_months = len(analysis.get("months_analyzed", []))
    repeating: List[Dict] = []
    suggested_new: List[Dict] = []

    for cat, data in analysis["by_category"].items():
        item = {
            "name": cat,
            "avg": round(data["avg"], 2),
            "months_present": data["months_present"],
            "trend": data["trend"],
            "expected_amount": round(data["avg"]),  # default = rounded average
        }
        if data["months_present"] >= threshold_months:
            repeating.append(item)
        elif data["months_present"] == threshold_months - 1 and total_months >= 3:
            suggested_new.append(item)

    # Sort biggest spenders first
    repeating.sort(key=lambda x: x["avg"], reverse=True)
    suggested_new.sort(key=lambda x: x["avg"], reverse=True)

    return repeating, suggested_new


# ---------------------------------------------------------------------------
# Budget computation
# ---------------------------------------------------------------------------

def compute_budget_breakdown(
    monthly_budget: float,
    repeating_categories: List[Dict],
    unexpected_expenses: List[Dict],
    carryover: float = 0.0,
) -> Dict[str, Any]:
    """
    Compute the full monthly budget breakdown.

    Args:
        monthly_budget:       Total budget for the month.
        repeating_categories: List of {name, expected_amount, ...}.
        unexpected_expenses:  List of {description, amount}.
        carryover:            Savings carried over from last month.

    Returns dict with:
      monthly_budget, carryover, total_available,
      repeating_total, unexpected_total, committed_total, discretionary,
      categories_breakdown, unexpected_breakdown
    """
    total_available = monthly_budget + carryover
    repeating_total = sum(float(c.get("expected_amount", 0)) for c in repeating_categories)
    unexpected_total = sum(float(e.get("amount", 0)) for e in unexpected_expenses)
    committed_total = repeating_total + unexpected_total
    discretionary = total_available - committed_total

    return {
        "monthly_budget": monthly_budget,
        "carryover": carryover,
        "total_available": total_available,
        "repeating_total": repeating_total,
        "unexpected_total": unexpected_total,
        "committed_total": committed_total,
        "discretionary": discretionary,
        "categories_breakdown": list(repeating_categories),
        "unexpected_breakdown": list(unexpected_expenses),
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_analysis_message(
    analysis: Dict[str, Any],
    repeating: List[Dict],
    suggested_new: List[Dict],
) -> str:
    """Format the spending analysis as a readable message for the user."""
    months = analysis.get("months_analyzed", [])
    months_str = ", ".join(months) if months else "recent months"
    total_repeating = sum(c["expected_amount"] for c in repeating)

    lines = [
        f"📊 Spending Analysis ({months_str})\n",
        f"Recurring categories — expected total: ₪{total_repeating:,.0f}",
    ]
    for cat in repeating:
        lines.append(
            f"  • {cat['name']}: ₪{cat['expected_amount']:,.0f} {cat['trend']} "
            f"(avg over {cat['months_present']} months)"
        )

    if suggested_new:
        lines.append("\nNewly detected (appeared recently — add to recurring?):")
        for cat in suggested_new:
            lines.append(
                f"  • {cat['name']}: ₪{cat['avg']:,.0f} {cat['trend']}"
            )

    lines += [
        "",
        "Review and adjust. Commands:",
        "  set <category> <amount>   — change expected amount",
        "  remove <category>         — remove from recurring",
        "  add <name> <amount>       — add a new recurring category",
        "  confirm <category>        — accept a suggested category",
        "  done                      — proceed to next step",
    ]
    return "\n".join(lines)


def format_breakdown_message(breakdown: Dict[str, Any]) -> str:
    """Format the final budget breakdown as a readable message."""
    lines = [
        "💰 Monthly Budget Breakdown\n",
        f"Monthly budget:    ₪{breakdown['monthly_budget']:,.0f}",
    ]
    if breakdown["carryover"]:
        lines.append(f"Carryover savings: ₪{breakdown['carryover']:,.0f}")
    lines.append(f"Total available:   ₪{breakdown['total_available']:,.0f}\n")

    lines.append("Recurring expenses:")
    for cat in breakdown["categories_breakdown"]:
        lines.append(f"  • {cat['name']}: ₪{cat['expected_amount']:,.0f}")
    lines.append(f"  Subtotal: ₪{breakdown['repeating_total']:,.0f}\n")

    if breakdown["unexpected_breakdown"]:
        lines.append("Upcoming one-off expenses:")
        for exp in breakdown["unexpected_breakdown"]:
            lines.append(f"  • {exp['description']}: ₪{exp['amount']:,.0f}")
        lines.append(f"  Subtotal: ₪{breakdown['unexpected_total']:,.0f}\n")

    disc = breakdown["discretionary"]
    disc_sign = "+" if disc >= 0 else ""
    status = "OK" if disc >= 0 else "OVER BUDGET"
    lines.append(f"Committed total:   ₪{breakdown['committed_total']:,.0f}")
    lines.append(f"Discretionary:     {disc_sign}₪{disc:,.0f}  [{status}]")

    if disc < 0:
        lines.append("\nYou're over budget. Consider reducing some categories.")
    else:
        lines.append("\nThis is what's left for unplanned spending and savings.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Category name matching (used by the workflow to apply user adjustments)
# ---------------------------------------------------------------------------

def _strip_emoji(text: str) -> str:
    """Remove emoji and punctuation for fuzzy matching."""
    return re.sub(r"[^\w\s]", "", text).strip().lower()


def find_category_by_name(name: str, categories: List[Dict]) -> Optional[Dict]:
    """
    Case-insensitive, emoji-tolerant search for a category in a list.
    Returns the matching dict or None.
    """
    query = _strip_emoji(name)
    for cat in categories:
        if query in _strip_emoji(cat["name"]) or _strip_emoji(cat["name"]) in query:
            return cat
    return None


# ---------------------------------------------------------------------------
# Persistent category preferences  (budget_data/repeating_categories.json)
# ---------------------------------------------------------------------------

_BUDGET_DATA_DIR = Path(__file__).parent.parent / "budget_data"
_PERSISTED_CATEGORIES_FILE = _BUDGET_DATA_DIR / "repeating_categories.json"


def load_persisted_categories() -> Tuple[List[Dict], Set[str]]:
    """
    Load the user's saved category preferences.

    Returns:
        confirmed  — List of {name, expected_amount} the user has confirmed as recurring.
        excluded   — Set of category names the user has explicitly removed (never suggest again).
    """
    if not _PERSISTED_CATEGORIES_FILE.exists():
        return [], set()
    try:
        data = json.loads(_PERSISTED_CATEGORIES_FILE.read_text(encoding="utf-8"))
        confirmed = data.get("confirmed", [])
        excluded = set(data.get("excluded", []))
        return confirmed, excluded
    except Exception as exc:
        logger.warning("Failed to load persisted categories: %s", exc)
        return [], set()


def save_persisted_categories(confirmed: List[Dict], excluded_names: Set[str]) -> None:
    """
    Save confirmed recurring categories and the excluded list to disk.

    Args:
        confirmed:      List of {name, expected_amount} to persist.
        excluded_names: Set of category names to never suggest as recurring.
    """
    _BUDGET_DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Only store name + expected_amount (keep the file lean)
    slim_confirmed = [{"name": c["name"], "expected_amount": c.get("expected_amount", 0)} for c in confirmed]
    data = {"confirmed": slim_confirmed, "excluded": sorted(excluded_names)}
    try:
        _PERSISTED_CATEGORIES_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("Persisted %d recurring categories, %d excluded.", len(slim_confirmed), len(excluded_names))
    except Exception as exc:
        logger.warning("Failed to save persisted categories: %s", exc)


def merge_categories_with_persisted(
    detected_repeating: List[Dict],
    detected_suggested: List[Dict],
    persisted_confirmed: List[Dict],
    excluded_names: Set[str],
) -> Tuple[List[Dict], List[Dict]]:
    """
    Merge Notion-detected categories with the user's saved preferences.

    Rules:
    - Persisted confirmed categories are always included in repeating.
      If Notion data has a newer average, update expected_amount toward it (but keep user's last set value).
    - Categories in excluded_names are never shown (repeating or suggested).
    - Newly detected categories not in persisted and not excluded go to suggested_new.

    Returns: (final_repeating, final_suggested)
    """
    # Index persisted by stripped name for matching
    persisted_index = {_strip_emoji(c["name"]): c for c in persisted_confirmed}
    # Index detected by stripped name for amount updates
    detected_index = {_strip_emoji(c["name"]): c for c in detected_repeating}

    final_repeating: List[Dict] = []
    seen_names: Set[str] = set()

    # 1. Start with persisted confirmed — always included, update avg if Notion has fresher data
    for p in persisted_confirmed:
        key = _strip_emoji(p["name"])
        if p["name"] in excluded_names or key in {_strip_emoji(e) for e in excluded_names}:
            continue  # user later excluded this — respect the exclusion
        entry = dict(p)
        if key in detected_index:
            d = detected_index[key]
            entry.setdefault("avg", d["avg"])
            entry.setdefault("months_present", d["months_present"])
            entry.setdefault("trend", d["trend"])
            # Update expected_amount if user hasn't manually set it
            # (we detect this if expected_amount == rounded avg from last save)
            if abs(entry["expected_amount"] - round(p["expected_amount"])) < 1:
                entry["expected_amount"] = round(d["avg"])
        else:
            entry.setdefault("avg", entry["expected_amount"])
            entry.setdefault("months_present", 0)
            entry.setdefault("trend", "→")
        final_repeating.append(entry)
        seen_names.add(key)

    # 2. Add newly detected that aren't persisted and aren't excluded
    excluded_stripped = {_strip_emoji(e) for e in excluded_names}
    for d in detected_repeating:
        key = _strip_emoji(d["name"])
        if key in seen_names or key in excluded_stripped:
            continue
        final_repeating.append(dict(d))
        seen_names.add(key)

    # 3. Suggested new: detected suggested, not excluded, not already repeating
    final_suggested: List[Dict] = []
    for s in detected_suggested:
        key = _strip_emoji(s["name"])
        if key in seen_names or key in excluded_stripped:
            continue
        final_suggested.append(dict(s))

    # Sort repeating biggest first
    final_repeating.sort(key=lambda x: x.get("expected_amount", 0), reverse=True)

    return final_repeating, final_suggested


# ---------------------------------------------------------------------------
# Smart projections + insights
# ---------------------------------------------------------------------------

# Sub-category keywords with no wiggle room (user can't easily reduce these)
_FIXED_KEYWORDS = {"rent", "bills", "electric", "insurance", "subscription", "mortgage", "loan"}

# Threshold above which we treat current-month pace as an impulse rather than a trend
_IMPULSE_MULTIPLIER = 1.4


def _is_fixed_category(name: str) -> bool:
    """Return True if the category name suggests a fixed, non-discretionary expense."""
    lower = name.lower()
    return any(k in lower for k in _FIXED_KEYWORDS)


def compute_smart_projections(
    budget_by_category: Dict[str, float],
    actual_by_category: Dict[str, float],
    habits_by_category: Dict[str, Any],
    repeating_confirmed: List[Dict],
    days_elapsed: int,
    days_in_month: int,
    actual_by_subcategory: Optional[Dict[str, float]] = None,
    habits_by_subcategory: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Compute end-of-month projections per category using three strategies:

    1. Fixed/recurring (Rent, Bills, Insurance, Electric, Subscriptions):
       Always expect the full historical avg — they haven't been paid yet doesn't mean
       they won't be. Projection = max(actual, historical_avg or budget).
       A category is also treated as fixed when all its subcategories have
       monthly_once cadence (detected from spending_habits.json).

    2. Impulse/variable (actual pace is >140% of expected pace vs history):
       One big purchase shouldn't extrapolate to the rest of the month.
       Projection = max(actual, historical_avg * 1.1).  Cap at a modest uplift from avg.

    3. Steady/linear (everything else):
       Projection = actual / progress_ratio.

    For monthly_once subcategories that haven't appeared yet this month,
    their historical avg is added to the parent category's baseline so the
    projection doesn't undercount expected fixed costs.

    Returns:
        {category: {actual, projected, budget, is_fixed, is_impulse, over_budget,
                    pct_over, has_wiggle_room, pending_subcats}}
    """
    progress = days_elapsed / days_in_month if days_in_month > 0 else 1.0
    repeating_names = {c["name"] for c in repeating_confirmed}
    actual_by_subcategory = actual_by_subcategory or {}
    habits_by_subcategory = habits_by_subcategory or {}

    # Pre-compute pending monthly_once subcategory amounts per parent category.
    # A subcategory is "pending" when:
    #   - cadence == "monthly_once"
    #   - not yet seen in actual_by_subcategory (or amount == 0)
    #   - typical_day is still ahead (or within a 5-day grace window)
    pending_by_category: Dict[str, float] = {}
    for sub, sub_habit in habits_by_subcategory.items():
        if sub_habit.get("cadence") != "monthly_once":
            continue
        if actual_by_subcategory.get(sub, 0.0) > 0:
            continue  # already paid this month
        typical_day = sub_habit.get("typical_day", 0)
        if typical_day > 0 and days_elapsed > typical_day + 5:
            continue  # past the grace window — may have been skipped this month
        parent = sub_habit.get("parent_category", "")
        if parent:
            pending_by_category[parent] = (
                pending_by_category.get(parent, 0.0) + sub_habit.get("avg", 0.0)
            )

    results: Dict[str, Dict[str, Any]] = {}

    all_cats = set(list(budget_by_category.keys()) + list(actual_by_category.keys()))
    for cat in all_cats:
        budget_val = budget_by_category.get(cat, 0.0)
        actual = actual_by_category.get(cat, 0.0)
        habit = habits_by_category.get(cat, {})
        historical_avg = habit.get("avg", 0.0)
        pending = pending_by_category.get(cat, 0.0)

        # Determine if all tracked subcategories for this category are monthly_once
        cat_subs_cadences = [
            sh.get("cadence")
            for sh in habits_by_subcategory.values()
            if sh.get("parent_category") == cat and sh.get("cadence")
        ]
        all_monthly_once = bool(cat_subs_cadences) and all(
            c == "monthly_once" for c in cat_subs_cadences
        )

        is_fixed = _is_fixed_category(cat) or all_monthly_once or (
            cat in repeating_names
            and historical_avg > 0
            and habit.get("max", 0) > 0
            and habit["max"] / max(habit.get("min", 1), 1) < 1.15
        )

        is_impulse = False
        if is_fixed:
            ref = historical_avg if historical_avg > 0 else budget_val
            # Bump ref up by any pending subcategory amounts not yet in actual
            if pending > 0 and actual < ref:
                ref = max(ref, actual + pending)
            projected = max(actual, ref)
        elif historical_avg > 0 and progress > 0:
            expected_pace = historical_avg * progress
            if actual > expected_pace * _IMPULSE_MULTIPLIER:
                # Impulse: cap projection at a modest uplift from avg, don't extrapolate
                projected = max(actual, historical_avg * 1.1)
                is_impulse = True
            else:
                # Add pending fixed subcats to linear base so they aren't missed
                base = actual + pending
                projected = base / progress if not pending else max(base / progress, actual + pending)
        else:
            base = actual + pending
            projected = base / progress if progress > 0 else base

        projected = round(projected, 2)
        pct_over = (projected - budget_val) / budget_val * 100 if budget_val > 0 else 0.0

        results[cat] = {
            "actual": actual,
            "projected": projected,
            "budget": budget_val,
            "is_fixed": is_fixed,
            "is_impulse": is_impulse,
            "over_budget": projected > budget_val,
            "pct_over": round(pct_over, 1),
            "has_wiggle_room": not is_fixed,
            "pending_subcats": round(pending, 2),
        }

    return results


def generate_budget_insights(
    projections: Dict[str, Dict[str, Any]],
    income: float,
    total_budget: float,
) -> List[str]:
    """
    Generate 1-3 actionable insights from the projections.

    Rules:
    - If over budget: identify biggest offender, find categories with wiggle room to cut,
      show savings impact (projected vs intended).
    - If on track: positive reinforcement only when clearly on track.
    - Never generate generic or obvious statements.
    """
    total_projected = sum(d["projected"] for d in projections.values())
    projected_savings = income - total_projected if income > 0 else 0.0
    intended_savings = income - total_budget if income > 0 else 0.0
    savings_gap = projected_savings - intended_savings  # negative = will save less

    insights: List[str] = []

    over_cats = sorted(
        [(cat, d) for cat, d in projections.items() if d["over_budget"] and d["budget"] > 0],
        key=lambda x: -(x[1]["projected"] - x[1]["budget"]),
    )
    saveable_cats = [
        (cat, d) for cat, d in projections.items()
        if d["has_wiggle_room"] and not d["over_budget"] and d["budget"] > 0
        and d["projected"] < d["budget"] * 0.9
    ]

    if over_cats:
        worst_cat, worst = over_cats[0]
        excess = worst["projected"] - worst["budget"]

        if worst["is_impulse"]:
            insights.append(
                f"🔴 *{worst_cat}* has a big purchase this month — projection capped at avg, "
                f"not extrapolating it. Watch it doesn't repeat."
            )
        else:
            insights.append(
                f"🔴 *{worst_cat}* is tracking ₪{excess:,.0f} over budget."
            )

        if savings_gap < -300 and saveable_cats:
            saveable_cat, saveable_d = saveable_cats[0]
            room = saveable_d["budget"] - saveable_d["projected"]
            insights.append(
                f"💡 You have ~₪{room:,.0f} of room in *{saveable_cat}* — "
                f"pulling back there could recover some of the gap."
            )

        if income > 0:
            insights.append(
                f"💰 Projected savings: *₪{projected_savings:,.0f}* "
                f"vs your target *₪{intended_savings:,.0f}*."
            )

    elif income > 0 and savings_gap >= 0:
        insights.append(
            f"🟢 On track. Projected savings: *₪{projected_savings:,.0f}* "
            f"— at or above your ₪{intended_savings:,.0f} target. Keep it up."
        )

    return insights[:3]


def find_savings_opportunities(
    projections: Dict[str, Dict[str, Any]],
    actual_by_subcategory: Dict[str, float],
    habits_by_subcategory: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    For over-budget categories that have wiggle room, identify which subcategories
    are driving the overspend and which ones could realistically be reduced.

    Only called when total projected > total budget. Returns a list of:
        {category, subcategory, actual, historical_avg, suggested_saving}
    sorted by suggested_saving descending.
    """
    over_wiggly = {cat for cat, d in projections.items() if d["over_budget"] and d["has_wiggle_room"]}
    if not over_wiggly:
        return []

    opportunities: List[Dict[str, Any]] = []

    for sub, actual in actual_by_subcategory.items():
        if actual <= 0:
            continue
        if _is_fixed_category(sub):
            continue

        habit = habits_by_subcategory.get(sub, {})
        hist_avg = habit.get("avg", 0.0)

        if hist_avg > 0 and actual > hist_avg * 1.1:
            saving = round(actual - hist_avg, 2)
            opportunities.append({
                "subcategory": sub,
                "actual": round(actual, 2),
                "historical_avg": round(hist_avg, 2),
                "suggested_saving": saving,
            })

    opportunities.sort(key=lambda x: -x["suggested_saving"])
    return opportunities[:5]  # top 5 only to keep message short


# ---------------------------------------------------------------------------
# Notion Budget DB — read + update
# ---------------------------------------------------------------------------

def fetch_current_month_budget() -> Dict[str, Any]:
    """
    Fetch the current month's budget page from Notion.

    Returns:
    {
      "page_id": str,
      "url": str,
      "month": "2026-04",
      "categories": {"Groceries 🛒": 1650.0, "Rent 💰": 2842.0, ...}
    }

    Raises ValueError if BUDGET_DATABASE_ID is missing.
    Raises RuntimeError if no page exists for the current month.
    """
    import os
    from notion_client import Client as NotionClient

    db_id = os.getenv("BUDGET_DATABASE_ID", "")
    if not db_id:
        raise ValueError("Missing BUDGET_DATABASE_ID environment variable.")

    notion_api_key = os.getenv("NOTION_API_KEY", "")
    if not notion_api_key:
        raise ValueError("Missing NOTION_API_KEY environment variable.")

    client = NotionClient(auth=notion_api_key)
    today = date.today()
    year_month = today.strftime("%Y-%m")            # e.g. "2026-04"
    first_of_month = today.replace(day=1).isoformat()  # e.g. "2026-04-01"

    # Filter: Date == first of current month
    try:
        resp = client.databases.query(
            database_id=db_id,
            filter={
                "property": "Date",
                "date": {"equals": first_of_month},
            },
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to query Budget DB: {exc}") from exc

    pages = resp.get("results", [])
    if not pages:
        raise RuntimeError(
            f"No budget page found for {year_month}. "
            "Create a page in your Budget DB with Date = first of the month."
        )

    page = pages[0]
    props = page.get("properties", {})
    categories: Dict[str, float] = {}

    for prop_name, prop_data in props.items():
        if prop_data.get("type") == "number":
            val = prop_data.get("number")
            if isinstance(val, (int, float)):
                categories[prop_name] = float(val)

    return {
        "page_id": page["id"],
        "url": page.get("url", ""),
        "month": year_month,
        "categories": categories,
    }


def update_budget_categories(page_id: str, updates: Dict[str, float]) -> None:
    """
    Update one or more category budget values on a Notion budget page.

    Args:
        page_id: Notion page ID of the budget page.
        updates: Dict of {category_name: new_amount}.
    """
    import os
    from notion_client import Client as NotionClient

    notion_api_key = os.getenv("NOTION_API_KEY", "")
    if not notion_api_key:
        raise ValueError("Missing NOTION_API_KEY environment variable.")

    client = NotionClient(auth=notion_api_key)
    properties = {name: {"number": amount} for name, amount in updates.items()}
    try:
        client.pages.update(page_id=page_id, properties=properties)
    except Exception as exc:
        raise RuntimeError(f"Failed to update Budget page: {exc}") from exc


# ---------------------------------------------------------------------------
# Notion Budget DB logging
# ---------------------------------------------------------------------------

def log_monthly_budget_to_notion(monthly_budget: float) -> str:
    """
    Create or update the current month's entry in the Notion Budget database.

    Behaviour:
    - Retrieves the DB schema to discover the title property name dynamically.
    - Queries all pages and looks for one whose title contains the current month
      label (e.g. "March 2026") OR whose date property falls in the current month.
    - If a matching page is found: updates its "Budget" (number) property.
    - If none is found: creates a new page with the month label as title.

    Required env var: BUDGET_DATABASE_ID
    Required DB property: Budget (number)

    Returns:
        The Notion page URL of the created/updated page, or "" on failure.
    """
    import os
    from notion_client import Client as NotionClient

    db_id = os.getenv("BUDGET_DATABASE_ID", "")
    if not db_id:
        raise ValueError("Missing BUDGET_DATABASE_ID environment variable.")

    notion_api_key = os.getenv("NOTION_API_KEY", "")
    if not notion_api_key:
        raise ValueError("Missing NOTION_API_KEY environment variable.")

    client = NotionClient(auth=notion_api_key)
    today = date.today()
    month_label = today.strftime("%B %Y")   # e.g. "March 2026"
    year_month = today.strftime("%Y-%m")    # e.g. "2026-03"

    # --- Discover title property name ---
    try:
        db_info = client.databases.retrieve(database_id=db_id)
        title_prop = next(
            (name for name, prop in db_info["properties"].items() if prop["type"] == "title"),
            "Name",
        )
    except Exception as exc:
        logger.warning("Could not retrieve Budget DB schema: %s", exc)
        title_prop = "Name"

    # --- Search for existing page for this month ---
    existing_page = None
    try:
        all_pages = client.databases.query(database_id=db_id).get("results", [])
        for page in all_pages:
            props = page.get("properties", {})

            # Match by title containing the month label
            title_data = props.get(title_prop, {}).get("title", [])
            title_text = "".join(t.get("plain_text", "") for t in title_data)
            if month_label.lower() in title_text.lower():
                existing_page = page
                break

            # Match by any date property falling in the current month
            for prop_data in props.values():
                if prop_data.get("type") == "date":
                    start = (prop_data.get("date") or {}).get("start", "")
                    if start[:7] == year_month:
                        existing_page = page
                        break
            if existing_page:
                break
    except Exception as exc:
        logger.warning("Could not query Budget DB: %s", exc)

    # --- Create or update ---
    budget_property = {"Budget": {"number": monthly_budget}}

    try:
        if existing_page:
            page = client.pages.update(
                page_id=existing_page["id"],
                properties=budget_property,
            )
            logger.info("Updated Budget DB page for %s: ₪%s", month_label, monthly_budget)
        else:
            page = client.pages.create(
                parent={"database_id": db_id},
                properties={
                    title_prop: {
                        "title": [{"type": "text", "text": {"content": month_label}}]
                    },
                    **budget_property,
                },
            )
            logger.info("Created Budget DB page for %s: ₪%s", month_label, monthly_budget)

        return page.get("url", "")
    except Exception as exc:
        logger.error("Failed to write to Budget DB: %s", exc)
        raise RuntimeError(f"Notion Budget DB update failed: {exc}") from exc
