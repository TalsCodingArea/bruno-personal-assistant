"""Receipt-channel PDF handler."""

import asyncio
import tempfile
from pathlib import Path

from financial_agent.domain.money import money
from financial_agent.services.expense_automation import (
    DEFAULT_EXPENSE_TIMEZONE,
    LoggedExpense,
)
from telegram import Update
from telegram.ext import ContextTypes

from bruno.config import BrunoSettings
from bruno.receipts import extract_receipt, receipt_expense_fields
from bruno.runtime import BrunoRuntime
from bruno.telegram_bot.automations import schedule_expense_checkup
from bruno.telegram_bot.logging import safe_log


async def handle_receipt_pdf(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    runtime: BrunoRuntime,
    settings: BrunoSettings,
) -> None:
    message = update.message or update.channel_post
    if message is None or message.document is None:
        return
    filename = message.document.file_name or "receipt.pdf"
    if not filename.lower().endswith(".pdf"):
        await message.reply_text("Please send a PDF receipt.")
        return
    await message.reply_text("Processing receipt...")
    temporary_path: Path | None = None
    try:
        telegram_file = await context.bot.get_file(message.document.file_id)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temporary:
            temporary_path = Path(temporary.name)
        await telegram_file.download_to_drive(custom_path=str(temporary_path))
        receipt = await asyncio.to_thread(
            extract_receipt,
            temporary_path,
            category_options=settings.receipt_category_options,
            model=settings.receipt_model,
        )
        currency = str(receipt.get("currency") or "ILS").upper()
        if currency not in {"ILS", "NIS", "₪"}:
            await message.reply_text(
                f"Receipt extracted as {currency}; it was not logged as an ILS expense."
            )
            return
        await runtime.start()
        if runtime.expenses is None:
            raise RuntimeError("Expense automation service is unavailable")
        logged, created = await store_receipt_expense(
            runtime,
            receipt,
            invoice_path=temporary_path,
            invoice_name=filename,
        )
        action = "logged" if created else "attached to existing expense"
        response = (
            f"✅ Receipt {action}\nVendor: {logged.description}\nTotal: ₪{logged.amount}\n"
            f"Category: {receipt.get('category')}"
        )
        if logged.url:
            response += f"\n{logged.url}"
        await message.reply_text(response)
        if created:
            schedule_expense_checkup(
                runtime, context, settings, fallback_chat=message.chat_id
            )
    except Exception as exc:
        await message.reply_text("I couldn't process this receipt PDF.")
        await safe_log(context, settings, f"[receipt:error] {exc}")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


async def store_receipt_expense(
    runtime: BrunoRuntime,
    receipt: dict[str, object],
    *,
    invoice_path: Path,
    invoice_name: str,
) -> tuple[LoggedExpense, bool]:
    """Attach a duplicate receipt to its expense, or create a new expense."""

    if runtime.expenses is None:
        raise RuntimeError("Receipt expense dependencies are unavailable")
    fields = receipt_expense_fields(receipt)
    lookup = await runtime.invoke_automation(
        "get_expenses_for_date",
        {
            "occurred_on": fields.get("occurred_on") or "",
            "timezone": DEFAULT_EXPENSE_TIMEZONE,
        },
    )
    occurred_on = lookup.get("occurred_on")
    candidates_value = lookup.get("expenses")
    if not isinstance(occurred_on, str) or not isinstance(candidates_value, list):
        raise TypeError("Expense lookup returned an invalid result")
    candidates = tuple(
        candidate for candidate in candidates_value if isinstance(candidate, dict)
    )
    fields["occurred_on"] = occurred_on
    receipt_amount = money(str(fields["amount"]))
    candidate = next(
        (
            item
            for item in candidates
            if item.get("occurred_on") == occurred_on
            and money(str(item.get("amount"))) == receipt_amount
        ),
        None,
    )
    if candidate is not None:
        matched_page_id = candidate.get("page_id")
        if not isinstance(matched_page_id, str) or not matched_page_id:
            raise TypeError("Matched expense is missing its page ID")
        logged = await runtime.expenses.attach_receipt(
            page_id=matched_page_id,
            description=str(candidate.get("description") or fields["description"]),
            amount=str(candidate.get("amount") or fields["amount"]),
            occurred_on=occurred_on,
            invoice_path=invoice_path,
            invoice_name=invoice_name,
        )
        return logged, False

    logged = await runtime.expenses.log_expense(
        **fields,
        invoice_path=invoice_path,
        invoice_name=invoice_name,
        timezone=DEFAULT_EXPENSE_TIMEZONE,
    )
    return logged, True
