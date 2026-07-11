"""
Tools that trigger multi-step workflows (job application and focused reviews).

These are created per-chat-session via factory functions so they hold
a reference to the shared app-level session state without circular imports.
"""

from langchain_core.messages import AIMessage
from langchain_core.tools import tool


def make_job_tool(chat_id: str, pending_jobs: dict):
    """Return an apply_for_job tool that queues the pipeline for app.py to execute."""

    @tool
    def apply_for_job(url: str) -> str:
        """
        Trigger the job application pipeline for a given job listing URL.
        Use this when the user provides a job listing URL or asks to apply for a job.
        The pipeline scrapes the listing, tailors the resume, writes a cover letter,
        generates a personal note, and logs the application to Notion.
        """
        pending_jobs[chat_id] = url
        return "Job application pipeline started — I'll send the documents shortly."

    return apply_for_job


def make_uncategorized_review_tool(uncategorized_review_graph):
    """Return a tool that runs the one-shot uncategorized expenses review workflow."""

    @tool
    async def start_uncategorized_review() -> str:
        """
        Review uncategorized Tal expenses.
        Use this when the user asks to review, categorize, or inspect uncategorized
        transactions. It syncs uncategorized Notion expenses into the ML review queue
        and returns every pending item with its suggested Category / Sub Category.
        Resolutions happen afterwards via resolve_expense_review per item.
        """
        from personal_assistant.agent.general.uncategorized_workflow import async_start_uncategorized_review

        state = await async_start_uncategorized_review(uncategorized_review_graph)
        msgs = state.get("messages", [])
        last_ai = next((m for m in reversed(msgs) if isinstance(m, AIMessage)), None)
        return last_ai.content if last_ai else "No uncategorized review result was generated."

    return start_uncategorized_review
