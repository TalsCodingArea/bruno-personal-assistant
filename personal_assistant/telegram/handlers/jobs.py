from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from telegram.ext import ContextTypes

from personal_assistant.telegram.logging import safe_log

logger = logging.getLogger("telegram-assistant")


async def handle_job_application(
    url: str,
    message,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Run the job application pipeline and deliver artifacts via Telegram."""
    from personal_assistant.agent.general.llm import get_llm
    from personal_assistant.tools.job_tools import run_job_application_workflow

    tmp_paths: List[Path] = []
    llm = get_llm()

    async def progress(msg: str) -> None:
        try:
            await message.reply_text(msg)
        except Exception as exc:
            logger.warning("Could not send progress message: %s", exc)

    try:
        result = await run_job_application_workflow(
            url=url,
            llm=llm,
            progress_callback=progress,
        )

        job_data = result["job_data"]
        resume_path: Path = result["resume_path"]
        cover_letter_path: Path = result["cover_letter_path"]
        personal_note: str = result["personal_note"]
        notion_url: str = result["notion_url"]

        tmp_paths.extend([resume_path, cover_letter_path])

        title = job_data.get("title", "Position")
        company = job_data.get("company", "Company")

        with resume_path.open("rb") as f:
            await message.reply_document(
                document=f,
                filename=f"Resume - {company}.pdf",
                caption=f"Resume tailored for {title} at {company}",
            )

        with cover_letter_path.open("rb") as f:
            await message.reply_document(
                document=f,
                filename=f"Cover Letter - {company}.pdf",
                caption=f"Cover letter for {title} at {company}",
            )

        if personal_note:
            await message.reply_text(f"Personal note:\n\n{personal_note}")

        summary_lines = ["Application logged to Notion."]
        if notion_url:
            summary_lines.append(f"Notion page: {notion_url}")
        await message.reply_text("\n".join(summary_lines))

        await safe_log(
            context,
            f"[job] Application pipeline complete: {title} at {company} | {url}",
        )

    except FileNotFoundError as exc:
        await message.reply_text(
            f"Setup incomplete: {exc}\n\n"
            "Fill in resume_data/user_profile.json and add your data to get started."
        )
    except RuntimeError as exc:
        logger.exception("Job application pipeline failed")
        await message.reply_text(f"The job application pipeline hit an error: {exc}")
        await safe_log(context, f"[job:error] {exc}")
    except Exception as exc:
        logger.exception("Job application pipeline failed unexpectedly")
        await message.reply_text("Something went wrong during the job application pipeline.")
        await safe_log(context, f"[job:error] {exc}")
    finally:
        for path in tmp_paths:
            try:
                if path and path.exists():
                    path.unlink()
            except Exception:
                pass
