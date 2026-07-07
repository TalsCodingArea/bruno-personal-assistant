from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Annotated, Any, Callable, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from agent.contexts.financial_advisor_context import FINANCIAL_ADVISOR_CONTEXT
from tools.financial_advisor.engine import (
    calculate_available_surplus,
    calculate_emergency_fund_target,
    calculate_future_expense_reserve,
    evaluate_desire_affordability,
    evaluate_emergency_fund,
    project_month_end_spending,
    score_desire,
)
from tools.financial_advisor.formatting import compact_reasons, ils

SubIntent = Literal[
    "general_finance_question",
    "expense_summary",
    "expense_drilldown",
    "transaction_lookup",
    "desire_affordability",
    "desire_capture",
    "future_purchase_lookup",
    "future_vacation_lookup",
    "saving_plan",
    "monthly_budget_advice",
    "future_expense_capture",
    "future_expense_review",
    "balance_update",
    "income_review",
    "emergency_fund_check",
    "savings_or_investing_readiness",
    "advisor_rule_update",
    "clarify",
]

AdvisorRoute = Literal["deterministic_evaluation", "contextual_answer", "clarify"]
ContextNeed = Literal[
    "rules",
    "budget",
    "expenses",
    "income",
    "balance",
    "future_purchases",
    "future_vacations",
    "future_expenses",
]


class FinancialAdvisorState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    user_text: str
    sub_intent: SubIntent
    route: AdvisorRoute
    context_needs: list[ContextNeed]
    period: dict[str, str]
    extracted: dict[str, Any]
    loaded_context: dict[str, Any]
    evaluation: dict[str, Any]
    write_plan: dict[str, Any]
    response: str
    needs_confirmation: bool


DataProvider = Callable[[SubIntent, dict[str, str], dict[str, Any]], dict[str, Any]]
WriteExecutor = Callable[[dict[str, Any]], dict[str, Any]]

_DESIRE_RE = re.compile(r"\b(i want|want to buy|thinking of buying|dreaming of|wish i had|can i afford|should i buy|smart to buy)\b", re.I)
_CAPTURE_DESIRE_RE = re.compile(r"\b(save|remember|capture|log)\b.*\b(desire|want|purchase|buy)\b", re.I)
_BALANCE_RE = re.compile(r"\b(balance|bank account|checking account|cash available)\b", re.I)
_FUTURE_EXPENSE_RE = re.compile(r"\b(yearly|annual|renewal|license|tuition|insurance|subscription|due in|every\s+\w+)\b", re.I)
_PAYMENT_RE = re.compile(r"\b(payment|pay|bill|cost)\b", re.I)
_EMERGENCY_RE = re.compile(r"\b(emergency fund|months of budget|safety net)\b", re.I)
_INCOME_RE = re.compile(r"\b(income|salary|paycheck|paid this month)\b", re.I)
_RULE_RE = re.compile(r"\b(i want|rule|from now on|do not let|don't let|keep).*\b(under|over|at least|months|percent|%)\b", re.I)
_DRILLDOWN_RE = re.compile(r"\b(why|driving|breakdown|high|too much)\b", re.I)
_TRANSACTION_RE = re.compile(r"\b(show|list|which|transactions?|expenses?)\b", re.I)
_SUMMARY_RE = re.compile(r"\b(how am i doing|recap|summary|overview|this month|month so far)\b", re.I)
_INVEST_RE = re.compile(r"\b(invest|investment|savings|move extra|extra cash)\b", re.I)
_FUTURE_PURCHASES_RE = re.compile(r"\b(future purchases|purchases i want|things i want|wishlist|future buys)\b", re.I)
_FUTURE_VACATIONS_RE = re.compile(r"\b(future vacations|vacations|trips|travel plans)\b", re.I)
_SAVING_PLAN_RE = re.compile(r"\b(save up|saving plan|plan to buy|save for|afford later|get it)\b", re.I)
_NUMBER_RE = re.compile(r"(?<!\w)(\d[\d,]*(?:\.\d+)?)\s*(?:ils|nis|₪)?", re.I)
_MONTHS_RE = re.compile(r"\b([2-9]|1[0-2])\s+months?\b", re.I)


_ROUTER_ALLOWED_SUB_INTENTS = set(SubIntent.__args__)  # type: ignore[attr-defined]
_ROUTER_ALLOWED_ROUTES = set(AdvisorRoute.__args__)  # type: ignore[attr-defined]
_ROUTER_ALLOWED_CONTEXT_NEEDS = set(ContextNeed.__args__)  # type: ignore[attr-defined]


def _month_period(today: date) -> dict[str, str]:
    start = today.replace(day=1)
    if today.month == 12:
        next_month = date(today.year + 1, 1, 1)
    else:
        next_month = date(today.year, today.month + 1, 1)
    end = date.fromordinal(next_month.toordinal() - 1)
    return {"start": start.isoformat(), "end": end.isoformat(), "month": f"{today.year:04d}-{today.month:02d}"}


def _today_from_config(config: RunnableConfig | None = None) -> date:
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    raw = configurable.get("today")
    if raw:
        try:
            return datetime.fromisoformat(str(raw)).date()
        except ValueError:
            pass
    return date.today()


def classify_financial_sub_intent(text: str) -> SubIntent:
    stripped = (text or "").strip()
    if not stripped:
        return "clarify"
    if _RULE_RE.search(stripped) and ("buy" not in stripped.lower()):
        return "advisor_rule_update"
    if _BALANCE_RE.search(stripped) and _NUMBER_RE.search(stripped):
        return "balance_update"
    if _EMERGENCY_RE.search(stripped):
        return "emergency_fund_check"
    if _FUTURE_EXPENSE_RE.search(stripped) or (_PAYMENT_RE.search(stripped) and _month_name_in_text(stripped)):
        if _NUMBER_RE.search(stripped):
            return "future_expense_capture"
        return "future_expense_review"
    if _FUTURE_PURCHASES_RE.search(stripped):
        return "future_purchase_lookup"
    if _FUTURE_VACATIONS_RE.search(stripped):
        return "future_vacation_lookup"
    if _SAVING_PLAN_RE.search(stripped):
        return "saving_plan"
    if _CAPTURE_DESIRE_RE.search(stripped):
        return "desire_capture"
    if _DESIRE_RE.search(stripped):
        return "desire_affordability"
    if _INVEST_RE.search(stripped):
        return "savings_or_investing_readiness"
    if _INCOME_RE.search(stripped):
        return "income_review"
    if "budget" in stripped.lower():
        return "monthly_budget_advice"
    if _SUMMARY_RE.search(stripped):
        return "expense_summary"
    if _TRANSACTION_RE.search(stripped):
        return "transaction_lookup"
    if _DRILLDOWN_RE.search(stripped):
        return "expense_drilldown"
    return "general_finance_question"


def _month_name_in_text(text: str) -> bool:
    return bool(
        re.search(
            r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b",
            text or "",
            re.I,
        )
    )


def route_for_sub_intent(sub_intent: SubIntent) -> AdvisorRoute:
    if sub_intent == "clarify":
        return "clarify"
    if sub_intent == "general_finance_question":
        return "contextual_answer"
    return "deterministic_evaluation"


def context_needs_for_sub_intent(sub_intent: SubIntent) -> list[ContextNeed]:
    if sub_intent == "clarify":
        return []

    needs: set[ContextNeed] = {"rules", "budget"}
    if sub_intent in {
        "expense_summary",
        "expense_drilldown",
        "transaction_lookup",
        "desire_affordability",
        "monthly_budget_advice",
        "savings_or_investing_readiness",
        "general_finance_question",
        "saving_plan",
    }:
        needs.add("expenses")
    if sub_intent in {
        "income_review",
        "monthly_budget_advice",
        "savings_or_investing_readiness",
        "general_finance_question",
        "desire_affordability",
        "saving_plan",
    }:
        needs.add("income")
    if sub_intent in {
        "desire_affordability",
        "balance_update",
        "emergency_fund_check",
        "savings_or_investing_readiness",
        "general_finance_question",
        "saving_plan",
    }:
        needs.add("balance")
    if sub_intent in {
        "desire_affordability",
        "future_expense_capture",
        "future_expense_review",
        "savings_or_investing_readiness",
        "general_finance_question",
    }:
        needs.add("future_expenses")
    if sub_intent in {
        "desire_capture",
        "desire_affordability",
        "future_purchase_lookup",
        "saving_plan",
        "general_finance_question",
    }:
        needs.add("future_purchases")
    if sub_intent in {"future_vacation_lookup", "saving_plan", "general_finance_question"}:
        needs.add("future_vacations")
    return sorted(needs)


def _first_amount(text: str) -> float | None:
    match = _NUMBER_RE.search(text or "")
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _extract_desire(text: str) -> dict[str, Any]:
    cost = _first_amount(text)
    cleaned = re.sub(_NUMBER_RE, "", text or "")
    cleaned = re.sub(r"\b(i want to buy|i want|want to buy|thinking of buying|can i afford|should i buy|a|an|new|probably|around|for|ils|nis)\b", " ", cleaned, flags=re.I)
    name = " ".join(cleaned.split()).strip(" .?") or "Financial desire"
    return {
        "name": name[:80],
        "estimated_cost": cost,
        "category": "Other",
        "desire_strength": 5,
        "necessity": "Nice to Have",
        "time_horizon": "Someday",
        "reason": text,
    }


def _extract_balance(text: str) -> dict[str, Any]:
    return {"account": "Main Checking", "balance": _first_amount(text), "currency": "ILS"}


def _extract_rule(text: str) -> dict[str, Any]:
    months = _MONTHS_RE.search(text or "")
    return {"rule": text.strip(), "emergency_fund_months": float(months.group(1)) if months else None}


def _extract_future_expense(text: str, today: date) -> dict[str, Any]:
    amount = _first_amount(text)
    month_match = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b",
        text or "",
        re.I,
    )
    month = None
    if month_match:
        month_names = [m.lower() for m in __import__("calendar").month_name]
        month_number = month_names.index(month_match.group(1).lower())
        year = today.year if month_number >= today.month else today.year + 1
        month = date(year, month_number, 1).isoformat()
    name = re.sub(_NUMBER_RE, "", text or "")
    name = re.sub(r"\b(every|yearly|annual|i need to pay|need to pay|for|in|due|ils|nis)\b", " ", name, flags=re.I)
    name = " ".join(name.split()).strip(" .?") or "Future expense"
    return {
        "name": name[:80],
        "amount": amount,
        "month": month,
    }


def _fallback_router_decision(text: str, today: date) -> dict[str, Any]:
    sub_intent = classify_financial_sub_intent(text)
    extracted: dict[str, Any] = {}
    if sub_intent in {"desire_affordability", "desire_capture", "saving_plan"}:
        extracted["desire"] = _extract_desire(text)
    elif sub_intent == "balance_update":
        extracted["balance_snapshot"] = _extract_balance(text)
    elif sub_intent in {"future_expense_capture", "future_expense_review"}:
        extracted["future_expense"] = _extract_future_expense(text, today)
    elif sub_intent == "advisor_rule_update":
        extracted["rule"] = _extract_rule(text)
    return {
        "sub_intent": sub_intent,
        "route": route_for_sub_intent(sub_intent),
        "context_needs": context_needs_for_sub_intent(sub_intent),
        "extracted": extracted,
    }


def _merge_extracted(fallback: dict[str, Any], routed: dict[str, Any]) -> dict[str, Any]:
    merged = dict(fallback)
    for key, value in routed.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(
                {
                    inner_key: inner_value
                    for inner_key, inner_value in value.items()
                    if inner_value is not None
                }
            )
            merged[key] = nested
        elif value is not None:
            merged[key] = value
    return merged


async def _llm_router_decision(llm: Any, text: str, today: date, config: RunnableConfig) -> dict[str, Any]:
    prompt = [
        SystemMessage(
            content=(
                "You are the routing layer for Tal's financial advisor capability graph.\n"
                "Return ONLY valid JSON with these keys:\n"
                "- sub_intent: one of "
                f"{sorted(_ROUTER_ALLOWED_SUB_INTENTS)}\n"
                "- route: one of ['deterministic_evaluation', 'contextual_answer', 'clarify']\n"
                "- context_needs: array using only "
                "['rules', 'budget', 'expenses', 'income', 'balance', "
                "'future_purchases', 'future_vacations', 'future_expenses']\n"
                "- extracted: object. Use keys desire, balance_snapshot, future_expense, rule when relevant.\n\n"
                "Use contextual_answer for broad finance questions that need context but not a specific calculation. "
                "Use deterministic_evaluation for affordability, emergency fund, spending summaries, budget advice, "
                "balance updates, future purchases, future vacations, saving plans, future expenses, income review, "
                "and savings/investing readiness. "
                "Use clarify only when the message is too vague to determine any finance task."
            )
        ),
        HumanMessage(content=f"Today: {today.isoformat()}\nUser message: {text}"),
    ]
    ai = await llm.ainvoke(prompt, config=config)
    parsed = json.loads(str(ai.content))
    sub_intent = parsed.get("sub_intent")
    route = parsed.get("route")
    if sub_intent not in _ROUTER_ALLOWED_SUB_INTENTS:
        raise ValueError(f"Invalid financial advisor sub_intent: {sub_intent}")
    if route not in _ROUTER_ALLOWED_ROUTES:
        raise ValueError(f"Invalid financial advisor route: {route}")

    context_needs = [
        need
        for need in parsed.get("context_needs", [])
        if need in _ROUTER_ALLOWED_CONTEXT_NEEDS
    ]
    if not context_needs and route != "clarify":
        context_needs = context_needs_for_sub_intent(sub_intent)

    routed_extracted = parsed.get("extracted") if isinstance(parsed.get("extracted"), dict) else {}
    fallback = _fallback_router_decision(text, today)
    extracted = _merge_extracted(fallback.get("extracted", {}), routed_extracted)
    return {
        "sub_intent": sub_intent,
        "route": route,
        "context_needs": context_needs,
        "extracted": extracted,
    }


def _default_provider(sub_intent: SubIntent, period: dict[str, str], extracted: dict[str, Any]) -> dict[str, Any]:
    from tools.financial_advisor.notion_tools import (
        get_expense_summary,
        get_future_expenses,
        get_future_purchases,
        get_future_vacations,
        get_income_summary,
        get_latest_account_balances,
        load_advisor_rules,
    )
    from tools.financial_advisor.memory import load_financial_profile

    needs = set(extracted.get("context_needs") or context_needs_for_sub_intent(sub_intent))
    context: dict[str, Any] = {"today": period["start"]}
    if "rules" in needs:
        context["advisor_rules"] = load_advisor_rules()
        context["advisor_profile"] = load_financial_profile()
    if "budget" in needs:
        context["budget"] = _load_monthly_budget_context(period["month"])
    if "expenses" in needs:
        context["expenses"] = get_expense_summary.invoke({"start_date": period["start"], "end_date": period["end"]})
    if "income" in needs:
        context["income"] = get_income_summary.invoke({"start_date": period["start"], "end_date": period["end"]})
    if "balance" in needs:
        context.update(get_latest_account_balances.invoke({"account": None}))
    if "future_expenses" in needs:
        try:
            context.update(get_future_expenses.invoke({}))
        except Exception:
            context["future_expenses"] = []
    if "future_purchases" in needs:
        context.update(get_future_purchases.invoke({"min_budget": None}))
    if "future_vacations" in needs:
        context.update(get_future_vacations.invoke({}))
    return context


def _load_monthly_budget_context(month: str) -> dict[str, Any]:
    try:
        from tools.monthly_budget.models import Month
        from tools.monthly_budget.notion_writer import list_budget_pages

        pages = list_budget_pages(Month.parse(month))
    except Exception as exc:
        return {"total": 0, "error": str(exc)}
    return {
        "total": round(sum(page.budget for page in pages), 2),
        "categories": {page.sub_category: {"budget": page.budget, "url": page.url} for page in pages},
    }


def _default_write_executor(write_plan: dict[str, Any]) -> dict[str, Any]:
    from tools.financial_advisor.notion_tools import (
        create_future_expense,
        create_future_purchase,
        update_financial_advisor_rule,
    )
    from tools.financial_advisor.memory import update_bank_account_balance

    action = write_plan.get("action")
    payload = write_plan.get("payload") or {}
    if not action or write_plan.get("requires_confirmation"):
        return {"ok": False, "skipped": True, "reason": "No low-risk write to execute."}
    if action == "create_future_purchase":
        return create_future_purchase.invoke(payload)
    if action == "create_future_expense":
        return create_future_expense.invoke(payload)
    if action == "update_bank_account_balance":
        return update_bank_account_balance.invoke(payload)
    if action == "update_financial_advisor_rule":
        return update_financial_advisor_rule.invoke(payload)
    return {"ok": False, "skipped": True, "reason": f"Unknown action {action}."}


def _latest_balance_value(context: dict[str, Any]) -> float | None:
    balances = context.get("balances") or []
    for item in balances:
        if str(item.get("account", "")).lower() != "investment":
            return item.get("balance")
    return None


def create_financial_advisor_graph(
    llm: Any | None = None,
    *,
    data_provider: DataProvider | None = None,
    write_executor: WriteExecutor | None = None,
):
    provider = data_provider or _default_provider
    executor = write_executor or _default_write_executor

    async def classify_request(state: FinancialAdvisorState, config: RunnableConfig) -> dict[str, Any]:
        today = _today_from_config(config)
        text = state.get("user_text") or next(
            (str(message.content) for message in reversed(state.get("messages", [])) if isinstance(message, HumanMessage)),
            "",
        )
        decision = _fallback_router_decision(text, today)
        if llm is not None:
            try:
                decision = await _llm_router_decision(llm, text, today, config)
            except Exception:
                decision = _fallback_router_decision(text, today)
        extracted = dict(decision.get("extracted", {}))
        extracted["context_needs"] = decision["context_needs"]
        return {
            "user_text": text,
            "sub_intent": decision["sub_intent"],
            "route": decision["route"],
            "context_needs": decision["context_needs"],
            "period": _month_period(today),
            "extracted": extracted,
        }

    async def load_context(state: FinancialAdvisorState, config: RunnableConfig) -> dict[str, Any]:
        context = provider(state["sub_intent"], state["period"], state.get("extracted", {}))
        return {"loaded_context": context}

    def route_after_context(state: FinancialAdvisorState) -> str:
        return state.get("route", "deterministic_evaluation")

    async def evaluate(state: FinancialAdvisorState, config: RunnableConfig) -> dict[str, Any]:
        sub_intent = state["sub_intent"]
        context = state.get("loaded_context", {})
        extracted = state.get("extracted", {})
        today = datetime.fromisoformat(state["period"]["start"]).date()
        evaluation: dict[str, Any] = {"sub_intent": sub_intent}

        if sub_intent in {"desire_affordability", "desire_capture"}:
            desire = extracted.get("desire", {})
            result = evaluate_desire_affordability(desire, {**context, "today": today.isoformat()})
            evaluation["affordability"] = result.to_dict()
            evaluation["priority_score"] = score_desire(desire, result)
        elif sub_intent in {"future_expense_capture", "future_expense_review"}:
            future_expense = extracted.get("future_expense", {})
            if future_expense.get("amount") and future_expense.get("month"):
                evaluation["reserve"] = calculate_future_expense_reserve(future_expense, today).to_dict()
            else:
                evaluation["missing"] = [
                    key for key in ("amount", "month") if not future_expense.get(key)
                ]
        elif sub_intent == "balance_update":
            snapshot = extracted.get("balance_snapshot", {})
            monthly_budget = float((context.get("budget") or {}).get("total") or 0)
            balance = snapshot.get("balance")
            if balance is not None and monthly_budget > 0:
                emergency = evaluate_emergency_fund(balance, monthly_budget)
                evaluation["emergency_fund"] = emergency.to_dict()
            else:
                evaluation["missing"] = ["current_month_budget"] if monthly_budget <= 0 else []
        elif sub_intent == "emergency_fund_check":
            monthly_budget = float((context.get("budget") or {}).get("total") or 0)
            balance = _latest_balance_value(context)
            if balance is None or monthly_budget <= 0:
                evaluation["missing"] = [
                    key for key, missing in {"latest_balance": balance is None, "current_month_budget": monthly_budget <= 0}.items() if missing
                ]
            else:
                evaluation["emergency_fund"] = evaluate_emergency_fund(balance, monthly_budget).to_dict()
        elif sub_intent == "savings_or_investing_readiness":
            monthly_budget = float((context.get("budget") or {}).get("total") or 0)
            balance = _latest_balance_value(context)
            target = calculate_emergency_fund_target(monthly_budget, 3)
            reserves = sum(
                calculate_future_expense_reserve(item, today).monthly_reserve
                for item in context.get("future_expenses", [])
            )
            evaluation["surplus"] = calculate_available_surplus(balance or 0, target, reserves)
            evaluation["emergency_target"] = target
            evaluation["latest_balance"] = balance
        elif sub_intent == "saving_plan":
            monthly_budget = float((context.get("budget") or {}).get("total") or 0)
            if monthly_budget <= 0:
                monthly_budget = float((context.get("expenses") or {}).get("total") or 0)
            balance = _latest_balance_value(context)
            months = float((context.get("advisor_profile") or {}).get("emergency_fund_months") or 3)
            target = calculate_emergency_fund_target(monthly_budget, months)
            desired = extracted.get("desire", {})
            cost = desired.get("estimated_cost")
            gap = max(0.0, target - float(balance or 0))
            surplus = max(0.0, float(balance or 0) - target)
            evaluation["saving_plan"] = {
                "monthly_baseline": monthly_budget,
                "emergency_fund_months": months,
                "emergency_target": target,
                "latest_balance": balance,
                "emergency_gap": round(gap, 2),
                "surplus_after_emergency": round(surplus, 2),
                "desired_item": desired.get("name"),
                "desired_cost": cost,
                "can_start_item_saving": gap <= 0,
            }
        elif sub_intent == "future_purchase_lookup":
            purchases = context.get("future_purchases", [])
            evaluation["future_purchases"] = {
                "count": len(purchases),
                "total_budget": round(sum(float(item.get("budget") or 0) for item in purchases), 2),
            }
        elif sub_intent == "future_vacation_lookup":
            vacations = context.get("future_vacations", [])
            evaluation["future_vacations"] = {
                "count": len(vacations),
                "total_budget": round(sum(float(item.get("budget") or 0) for item in vacations), 2),
            }
        elif sub_intent in {"expense_summary", "expense_drilldown", "transaction_lookup", "monthly_budget_advice"}:
            expenses = context.get("expenses", {})
            evaluation["expense_total"] = expenses.get("total")
            evaluation["by_category"] = expenses.get("by_category", {})
            evaluation["projection"] = project_month_end_spending(expenses, {}, today)
        return {"evaluation": evaluation}

    async def plan_writes(state: FinancialAdvisorState, config: RunnableConfig) -> dict[str, Any]:
        sub_intent = state["sub_intent"]
        extracted = state.get("extracted", {})
        evaluation = state.get("evaluation", {})
        write_plan: dict[str, Any] = {"action": None, "requires_confirmation": False, "executed": None}

        if sub_intent in {"desire_affordability", "desire_capture"}:
            desire = dict(extracted.get("desire", {}))
            affordability = evaluation.get("affordability", {})
            advisor_notes = compact_reasons(affordability.get("reasons", []))
            reason = desire.get("reason", "")
            if advisor_notes:
                reason = f"{reason}\nAdvisor: {advisor_notes}" if reason else advisor_notes
            if sub_intent == "desire_capture" or affordability.get("level") != "affordable_now":
                write_plan = {
                    "action": "create_future_purchase",
                    "payload": {
                        "name": desire.get("name") or "Future purchase",
                        "budget": desire.get("estimated_cost"),
                        "reason": reason,
                    },
                    "requires_confirmation": False,
                }
        elif sub_intent == "future_expense_capture":
            future_expense = dict(extracted.get("future_expense", {}))
            if evaluation.get("reserve"):
                write_plan = {
                    "action": "create_future_expense",
                    "payload": {
                        "name": future_expense.get("name") or "Future expense",
                        "amount": future_expense.get("amount"),
                        "month": future_expense.get("month"),
                    },
                    "requires_confirmation": False,
                }
        elif sub_intent == "balance_update":
            snapshot = extracted.get("balance_snapshot", {})
            if snapshot.get("balance") is not None:
                write_plan = {
                    "action": "update_bank_account_balance",
                    "payload": {
                        "balance": snapshot.get("balance"),
                        "currency": snapshot.get("currency", "ILS"),
                        "notes": "Updated from personal assistant chat.",
                    },
                    "requires_confirmation": False,
                }
        elif sub_intent == "advisor_rule_update":
            rule = extracted.get("rule", {})
            if rule.get("rule"):
                write_plan = {
                    "action": "update_financial_advisor_rule",
                    "payload": {"rule": rule["rule"]},
                    "requires_confirmation": False,
                }

        if write_plan.get("action") and not write_plan.get("requires_confirmation"):
            try:
                write_plan["executed"] = executor(write_plan)
            except Exception as exc:
                write_plan["executed"] = {"ok": False, "error": str(exc)}
        return {"write_plan": write_plan, "needs_confirmation": bool(write_plan.get("requires_confirmation"))}

    async def respond(state: FinancialAdvisorState, config: RunnableConfig) -> dict[str, Any]:
        if llm is not None:
            prompt = [
                SystemMessage(content=FINANCIAL_ADVISOR_CONTEXT.strip()),
                HumanMessage(
                    content=(
                        f"User: {state.get('user_text')}\n"
                        f"Sub-intent: {state.get('sub_intent')}\n"
                        f"Route: {state.get('route')}\n"
                        f"Extracted: {state.get('extracted')}\n"
                        f"Loaded context: {state.get('loaded_context')}\n"
                        f"Evaluation: {state.get('evaluation')}\n"
                        f"Write plan: {state.get('write_plan')}\n"
                        "Compose the concise financial advisor response."
                    )
                ),
            ]
            ai = await llm.ainvoke(prompt, config=config)
            response = str(ai.content)
            return {"messages": [AIMessage(content=response)], "response": response}

        response = _fallback_response(state)
        return {"messages": [AIMessage(content=response)], "response": response}

    graph = StateGraph(FinancialAdvisorState)
    graph.add_node("classify_request", classify_request)
    graph.add_node("load_context", load_context)
    graph.add_node("evaluate", evaluate)
    graph.add_node("plan_writes", plan_writes)
    graph.add_node("respond", respond)
    graph.set_entry_point("classify_request")
    graph.add_edge("classify_request", "load_context")
    graph.add_conditional_edges(
        "load_context",
        route_after_context,
        {
            "deterministic_evaluation": "evaluate",
            "contextual_answer": "respond",
            "clarify": "respond",
        },
    )
    graph.add_edge("evaluate", "plan_writes")
    graph.add_edge("plan_writes", "respond")
    graph.add_edge("respond", END)
    return graph.compile()


def _fallback_response(state: FinancialAdvisorState) -> str:
    sub_intent = state.get("sub_intent")
    evaluation = state.get("evaluation", {})
    write_plan = state.get("write_plan", {})
    executed = write_plan.get("executed") or {}

    if sub_intent in {"desire_affordability", "desire_capture"}:
        affordability = evaluation.get("affordability", {})
        level = affordability.get("level")
        if level == "needs_more_info":
            return f"I need one missing value before judging this: {', '.join(affordability.get('missing', []))}."
        saved = " I saved it to Future Purchases." if executed.get("ok") else ""
        return (
            f"Short answer: {level.replace('_', ' ') if level else 'unclear'}.\n\n"
            f"Cost: {ils(affordability.get('estimated_cost'))}. "
            f"Emergency target: {ils(affordability.get('emergency_target'))}. "
            f"Latest balance: {ils(affordability.get('latest_balance'))}. "
            f"Safe surplus after reserves: {ils(affordability.get('available_after_emergency'))}.\n"
            f"{compact_reasons(affordability.get('reasons', []))}{saved}"
        )
    if sub_intent == "future_expense_capture":
        if evaluation.get("missing"):
            return f"I can track this future expense, but I still need: {', '.join(evaluation['missing'])}."
        reserve = evaluation.get("reserve", {})
        saved = " I saved it to Notion." if executed.get("ok") else ""
        return (
            f"Got it. This needs a reserve of {ils(reserve.get('monthly_reserve'))}/month "
            f"for {reserve.get('months_remaining')} month(s), based on {ils(reserve.get('amount'))} "
            f"due on {reserve.get('due_date')}.{saved}"
        )
    if sub_intent == "balance_update":
        emergency = evaluation.get("emergency_fund")
        saved = " I updated the remembered bank balance." if executed.get("ok") else ""
        if emergency:
            return (
                f"Balance noted.{saved} Emergency target is {ils(emergency.get('target'))}. "
                f"Your current surplus is {ils(emergency.get('surplus'))} and gap is {ils(emergency.get('gap'))}."
            )
        return f"Balance noted.{saved} I still need the current monthly budget to evaluate the emergency fund."
    if sub_intent == "emergency_fund_check":
        emergency = evaluation.get("emergency_fund")
        if not emergency:
            return f"I need {', '.join(evaluation.get('missing', []))} to check the emergency fund."
        return (
            f"Emergency target: {ils(emergency.get('target'))}. "
            f"Latest balance: {ils(emergency.get('balance'))}. "
            f"Gap: {ils(emergency.get('gap'))}. Surplus: {ils(emergency.get('surplus'))}."
        )
    if sub_intent == "advisor_rule_update":
        return "Saved this as a financial advisor rule." if executed.get("ok") else "I prepared this rule, but could not save it yet."
    if sub_intent == "savings_or_investing_readiness":
        surplus = evaluation.get("surplus")
        if surplus and surplus > 0:
            return f"You have {ils(surplus)} beyond the emergency target and reserves. Reasonable options are savings, desire funding, or reviewing investment readiness."
        return "I do not see investable surplus yet after the emergency fund target and known reserves."
    if sub_intent == "saving_plan":
        plan = evaluation.get("saving_plan", {})
        if plan.get("latest_balance") is None:
            return "I need your current bank account balance before I can build a saving plan."
        if plan.get("emergency_gap", 0) > 0:
            return (
                f"First priority: rebuild the bank buffer.\n\n"
                f"Your target is {ils(plan.get('emergency_target'))} "
                f"({plan.get('emergency_fund_months', 3):g} months x {ils(plan.get('monthly_baseline'))}). "
                f"Your remembered balance is {ils(plan.get('latest_balance'))}, so the gap is "
                f"{ils(plan.get('emergency_gap'))}. After that, we can save toward "
                f"{plan.get('desired_item') or 'the purchase'}."
            )
        return (
            f"You are above the emergency buffer by {ils(plan.get('surplus_after_emergency'))}. "
            f"We can start planning for {plan.get('desired_item') or 'the purchase'}"
            f"{' at ' + ils(plan.get('desired_cost')) if plan.get('desired_cost') else ''}."
        )
    if sub_intent == "future_purchase_lookup":
        purchases = (state.get("loaded_context", {}) or {}).get("future_purchases", [])
        if not purchases:
            return "You do not have any Future Purchases saved right now."
        lines = ["Current Future Purchases:"]
        for item in purchases[:10]:
            lines.append(f"- {item.get('name')}: {ils(item.get('budget'))}")
        return "\n".join(lines)
    if sub_intent == "future_vacation_lookup":
        vacations = (state.get("loaded_context", {}) or {}).get("future_vacations", [])
        if not vacations:
            return "You do not have any Future Vacations saved right now."
        lines = ["Current Future Vacations:"]
        for item in vacations[:10]:
            recommended = item.get("recommended_time")
            time_text = f" (best time: {recommended})" if recommended else ""
            lines.append(f"- {item.get('country')}: {ils(item.get('budget'))}{time_text}")
        return "\n".join(lines)
    if sub_intent in {"expense_summary", "expense_drilldown", "transaction_lookup", "monthly_budget_advice"}:
        projection = evaluation.get("projection", {})
        return (
            f"This month you have spent {ils(evaluation.get('expense_total'))}. "
            f"Projected month-end spend is {ils(projection.get('projected_month_total'))}."
        )
    if sub_intent == "general_finance_question":
        context = state.get("loaded_context", {})
        budget = context.get("budget") or {}
        expenses = context.get("expenses") or {}
        income = context.get("income") or {}
        balances = context.get("balances") or []
        latest_balance = balances[0].get("balance") if balances else None
        return (
            "I can answer this as a financial advisor from the context I loaded.\n\n"
            f"Current month income: {ils(income.get('total'))}. "
            f"Current month expenses: {ils(expenses.get('total'))}. "
            f"Monthly budget: {ils(budget.get('total'))}. "
            f"Latest liquid balance: {ils(latest_balance)}.\n"
            "For a more concrete recommendation, ask about affordability, emergency fund, spending, or savings readiness."
        )
    return "I need one clearer financial question to evaluate this properly."


class FinancialAdvisorRuntime:
    def __init__(self, graph) -> None:
        self._graph = graph

    async def ainvoke(self, inputs: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        user_text = str(inputs.get("input", ""))
        state = await self._graph.ainvoke(
            {"messages": [HumanMessage(content=user_text)], "user_text": user_text},
            config=config or {},
        )
        return {"output": state.get("response", ""), "state": state}
