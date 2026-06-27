from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from personal_assistant.config import settings
from personal_assistant.telegram.formatting import markdown_v2_safe
from personal_assistant.telegram.logging import safe_log

logger = logging.getLogger("telegram-assistant")


async def handle_receipt_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from tools.notion_tools import (
        attach_file_to_notion_file_upload,
        notion_create_database_page,
        notion_create_file_upload,
        notion_properties_from_receipt,
    )
    from tools.receipt_tools import receipt_extract_summary_from_pdf

    message = update.message
    if "channel_post" in update._get_attrs():
        message = update.channel_post
    if not message or not message.document:
        return

    document = message.document
    filename = document.file_name or "receipt.pdf"
    if not filename.lower().endswith(".pdf"):
        await message.reply_text("Please send a PDF receipt.")
        return

    await message.reply_text("Processing receipt...")
    tmp_path: Optional[Path] = None

    try:
        telegram_file = await context.bot.get_file(document.file_id)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        await telegram_file.download_to_drive(custom_path=str(tmp_path))

        receipt_data = receipt_extract_summary_from_pdf.invoke(
            {
                "pdf_path": str(tmp_path),
                "category_options": settings.receipt_category_options,
            }
        )

        if not isinstance(receipt_data, dict):
            raise ValueError("Receipt extraction returned an unexpected result type.")

        vendor = markdown_v2_safe(receipt_data.get("vendor"))
        total = markdown_v2_safe(receipt_data.get("total_amount"))
        category = markdown_v2_safe(receipt_data.get("category"))
        pdf_type = markdown_v2_safe(receipt_data.get("source_pdf_type"))

        summary = (
            "✅ Receipt processed\\.\n"
            f"*Vendor*: {vendor}\n"
            f"*Total*: {total}\n"
            f"*Category*: {category}\n"
            f"*PDF Type*: {pdf_type}"
        )
        await message.reply_text(summary, parse_mode="MarkdownV2")

        if os.getenv("EXPENSES_DATABASE_ID", ""):
            notion_properties = notion_properties_from_receipt(receipt_data)
            file_upload = notion_create_file_upload()
            if not isinstance(file_upload, dict) or "file_upload_id" not in file_upload or not file_upload["ok"]:
                await safe_log(context, f"[receipt:notion] Failed to upload file to Notion: {file_upload}")
                return

            file_date = notion_properties.get("Date", {}).get("content", {}).get("start", "Unknown Date")
            file_name = f"{receipt_data.get('vendor') or 'Receipt'} - {file_date}.pdf"
            attach_file_to_notion_file_upload(
                file_upload["file_upload_id"],
                file_path=str(tmp_path),
                file_name=file_name,
            )
            if notion_properties:
                create_res = notion_create_database_page.invoke(
                    {
                        "database_id": os.getenv("EXPENSES_DATABASE_ID", ""),
                        "properties": notion_properties,
                        "file_property_name": "Invoice",
                        "file_upload_id": file_upload["file_upload_id"],
                        "file_name": file_name,
                    }
                )
                await safe_log(context, f"[receipt:notion] Created Notion page: {create_res}")
            else:
                await safe_log(context, "[receipt:notion] Skipped Notion write due to empty mapped properties.")

        await safe_log(context, f"[receipt] {json.dumps(receipt_data, ensure_ascii=False)}")
    except Exception as exc:
        logger.exception("Receipt processing failed")
        await message.reply_text("I couldn't process this receipt PDF.")
        await safe_log(context, f"[receipt:error] {exc}")
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
