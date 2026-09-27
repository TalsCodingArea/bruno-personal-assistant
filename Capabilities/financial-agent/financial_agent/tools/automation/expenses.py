"""Trusted tool adapters for expense ingestion."""

import re
from dataclasses import asdict
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from financial_agent.integrations.jev import TransactionNotificationDecision
from financial_agent.services.expense_automation import (
    DEFAULT_EXPENSE_TIMEZONE,
    ExpenseAutomationService,
    LoggedExpense,
)

_NON_SHEKEL_CURRENCY_RE = re.compile(
    r"(\$|€|£|\bUSD\b|\bEUR\b|\bGBP\b|\bDOLLARS?\b|\bEUROS?\b)",
    re.IGNORECASE,
)
_SHEKEL_CURRENCY_RE = re.compile(
    r'(₪|\bILS\b|\bNIS\b|ש\s*"?\s*ח|שקל)',
    re.IGNORECASE,
)
_AMOUNT_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


class ExpenseTextModel(Protocol):
    def with_structured_output(self, schema: type[BaseModel]) -> Any: ...


class TransactionNotificationClassifier(Protocol):
    async def classify_notification(
        self, text: str
    ) -> TransactionNotificationDecision: ...


class ExtractedExpense(BaseModel):
    """One ILS card transaction extracted from an automation message."""

    description: str = Field(description="Merchant name, without the card issuer")
    amount: float = Field(gt=0, description="Transaction amount in Israeli shekels")


def build_expense_automation_tools(
    service: ExpenseAutomationService,
    *,
    text_model: ExpenseTextModel | None = None,
    transaction_classifier: TransactionNotificationClassifier | None = None,
) -> tuple[BaseTool, ...]:
    """Build tools that may only be registered in a trusted automation channel."""

    @tool("get_expenses_for_date")
    async def get_expenses_for_date(
        occurred_on: str = "",
        timezone: str = DEFAULT_EXPENSE_TIMEZONE,
    ) -> dict[str, object]:
        """Return every existing expense from one date for trusted duplicate checks."""

        target = service.expense_date(occurred_on or None, timezone=timezone)
        expenses = await service.expenses_on(target, timezone=timezone)
        return {
            "occurred_on": target.isoformat(),
            "expenses": [asdict(expense) for expense in expenses],
        }

    @tool("log_expense")
    async def log_expense(
        description: str,
        amount: str,
        occurred_on: str = "",
        category: list[str] | None = None,
        subcategory: list[str] | None = None,
        payment_method: str = "Credit",
        expense_type: str = "Need",
        tags: list[str] | None = None,
        timezone: str = DEFAULT_EXPENSE_TIMEZONE,
    ) -> dict[str, object]:
        """Log one trusted ILS expense using the supplied GMT/UTC offset."""

        result = await service.log_expense(
            description=description,
            amount=amount,
            occurred_on=occurred_on or None,
            category=category or ("Uncategorized",),
            subcategory=subcategory or (),
            payment_method=payment_method,
            expense_type=expense_type,
            tags=tags,
            timezone=timezone,
        )
        return asdict(result)

    @tool("auto_expense_tool")
    async def auto_expense_tool(
        description: str,
        amount: float | str,
        tag: str = "Tal 👨🏻",
        tags: list[str] | None = None,
        timezone: str = DEFAULT_EXPENSE_TIMEZONE,
    ) -> dict[str, object]:
        """Log today's uncategorized ILS card expense; skip explicit foreign currency."""

        amount_value = _coerce_auto_expense_amount(amount)
        if amount_value is None:
            return {
                "status": "skipped",
                "message": f"Skipped non-shekel expense: {description.strip()} — {amount}",
            }
        result = await service.log_expense(
            description=description,
            amount=str(amount_value),
            category=("Uncategorized",),
            tags=tags or (tag,),
            timezone=timezone,
        )
        return asdict(result)

    @tool("log_txt_expense")
    async def log_txt_expense(
        text: str,
        tag: str = "Tal 👨🏻",
        tags: list[str] | None = None,
        timezone: str = DEFAULT_EXPENSE_TIMEZONE,
    ) -> dict[str, object]:
        """Extract one ILS card transaction with an LLM and log it uncategorized."""

        result = await _extract_and_log_expense(
            service,
            text_model,
            text=text,
            tag=tag,
            tags=tags,
            timezone=timezone,
        )
        return asdict(result)

    @tool("handle_cal_notification")
    async def handle_cal_notification(
        text: str,
        tag: str = "Tal 👨🏻",
        tags: list[str] | None = None,
        timezone: str = DEFAULT_EXPENSE_TIMEZONE,
    ) -> dict[str, object]:
        """Classify a Cal phone notification and log it only when it is an expense."""

        if transaction_classifier is None:
            raise RuntimeError(
                "Cal notification automation requires TYPESAFE_API_KEY for Jev"
            )
        message_text = text.strip()
        if not message_text:
            raise ValueError("text cannot be empty")
        decision = await transaction_classifier.classify_notification(message_text)
        if not decision.is_transaction:
            return {
                "status": "ignored",
                "message": "Ignored Cal notification: it is not an expense transaction.",
                "transaction_probability": decision.probability,
                "classifier_model": decision.model,
            }
        result = await _extract_and_log_expense(
            service,
            text_model,
            text=message_text,
            tag=tag,
            tags=tags,
            timezone=timezone,
        )
        return {
            **asdict(result),
            "transaction_probability": decision.probability,
            "classifier_model": decision.model,
        }

    return (
        get_expenses_for_date,
        log_expense,
        auto_expense_tool,
        log_txt_expense,
        handle_cal_notification,
    )


async def _extract_and_log_expense(
    service: ExpenseAutomationService,
    text_model: ExpenseTextModel | None,
    *,
    text: str,
    tag: str,
    tags: list[str] | None,
    timezone: str,
) -> LoggedExpense:
    if text_model is None:
        raise RuntimeError("Text-expense automation requires an extraction model")
    message_text = text.strip()
    if not message_text:
        raise ValueError("text cannot be empty")
    extractor = text_model.with_structured_output(ExtractedExpense)
    parsed = await extractor.ainvoke(
        [
            SystemMessage(
                content=(
                    "You extract one Israeli credit-card transaction. "
                    "Return the merchant name and ILS amount only."
                )
            ),
            HumanMessage(
                content=(
                    "Extract one transaction from this phone notification. The description "
                    "is the merchant, not the card company. In Hebrew card messages, remove "
                    "the leading ב from a merchant appearing after the card suffix. "
                    "Do not infer category or subcategory.\n\nNotification:\n"
                    f"{message_text}"
                )
            ),
        ]
    )
    if not isinstance(parsed, ExtractedExpense):
        parsed = ExtractedExpense.model_validate(parsed)
    return await service.log_expense(
        description=parsed.description,
        amount=str(parsed.amount),
        category=("Uncategorized",),
        tags=tags or (tag,),
        timezone=timezone,
    )


def _coerce_auto_expense_amount(amount: float | str) -> float | None:
    if isinstance(amount, (int, float)) and not isinstance(amount, bool):
        return float(amount)
    if not isinstance(amount, str):
        raise ValueError("amount must be a number or shekel amount string")
    value = amount.strip()
    if not value:
        raise ValueError("amount must be a number or shekel amount string")
    if _NON_SHEKEL_CURRENCY_RE.search(value):
        return None
    if not _SHEKEL_CURRENCY_RE.search(value):
        raise ValueError("String amount must include a shekel currency marker")
    match = _AMOUNT_NUMBER_RE.search(value)
    if match is None:
        raise ValueError("String amount must include a numeric amount")
    parsed = float(match.group(0).replace(",", ""))
    if parsed <= 0:
        raise ValueError("amount must be positive")
    return parsed
