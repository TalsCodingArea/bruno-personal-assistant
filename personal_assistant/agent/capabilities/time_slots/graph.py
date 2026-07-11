"""LangGraph workflow that re-summarizes a time block's name from its tasks.

Triggered from the Telegram automations channel with the changed page's URL:

    {"tool": "update_time_slot_name", "args": {"url": "https://notion.so/..."}}

Graph shape (conditional edges let it exit early when there's nothing to do):

    fetch_slot ──(no tasks)──────────────► respond
        │
        ▼
    fetch_tasks ──► gather_context ──► propose_name
                                            │
                              (name unchanged)──► respond
                                            │
                                            ▼
                                       apply_update ──► respond

Every Notion read/write is injected (defaults in notion_io.py), and the LLM
call is injected as well, so the whole graph is unit-testable with fakes --
same pattern as the uncategorized review workflow.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph

from personal_assistant.agent.capabilities.time_slots import naming, notion_io

logger = logging.getLogger("personal-assistant.time-slots")

ProposeFn = Callable[[str, str], Awaitable[str]]

_MAX_TASKS = 30  # sanity cap; a time block should never hold anywhere near this


class TimeSlotRenameState(TypedDict, total=False):
    slot_url: str
    slot: dict[str, Any]
    tasks: list[dict[str, Any]]
    examples: list[dict[str, str]]
    course_short_names: dict[str, str]
    proposed_name: str
    learned_short_names: dict[str, str]
    updated: bool
    message: str


_SYSTEM_PROMPT = """\
You name time blocks in Tal's university calendar. A time block's name must
summarize the tasks planned inside it, readable at a glance in a calendar app.

Naming conventions (learn the exact style from the examples provided):
- Assignments are named "<Course short name> - Ex.<n> ..." with question
  numbers summarized: contiguous ranges collapse ("Q1-Q4"), gaps stay listed
  ("Q1,Q3"). Example: "Numeric - Ex.4 Q1-Q4".
- Other task styles exist too, e.g. "Watch Comp Lec 5" or
  "3 questions from Data Ex.3" -- match whatever the examples show.
- Course short names: the mapping given to you is authoritative -- always use
  it. For a course not in the mapping, derive a short, recognizable one-word
  name from the full course name (e.g. "Numeric Analysis" -> "Numeric").
- Keep it short (aim under 40 characters), no dates, no filler words.
- If tasks span multiple courses, mention each course group briefly,
  separated by " + ".

Respond with ONLY a JSON object, no markdown fence:
{"name": "<the block name>",
 "course_short_names": {"<full course name>": "<short name you used>", ...}}
"""


def _build_user_prompt(state: TimeSlotRenameState) -> str:
    slot = state.get("slot", {})
    tasks = state.get("tasks", [])

    task_lines = [
        f"- {task['name']} (course: {task.get('course') or 'unknown'}, status: {task.get('status') or 'unknown'})"
        for task in tasks
    ]
    example_lines = [
        f'- tasks "{example["tasks"]}" -> named "{example["name"]}"'
        for example in state.get("examples", [])
    ]
    mapping = state.get("course_short_names", {})

    sections = [
        f"Current block name: {slot.get('name') or '(unnamed)'}",
        "Tasks currently in this block:\n" + ("\n".join(task_lines) or "(none)"),
    ]
    if mapping:
        sections.append("Authoritative course short names:\n" + json.dumps(mapping, ensure_ascii=False))
    if example_lines:
        sections.append("Examples of my naming style:\n" + "\n".join(example_lines))
    sections.append("Name this block.")
    return "\n\n".join(sections)


def _parse_llm_response(raw: str) -> tuple[str, dict[str, str]]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
    payload = json.loads(text)
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("LLM returned an empty block name.")
    short_names = payload.get("course_short_names") or {}
    if not isinstance(short_names, dict):
        short_names = {}
    return name, {str(k): str(v) for k, v in short_names.items()}


async def _default_propose(system_prompt: str, user_prompt: str) -> str:
    from personal_assistant.agent.general.llm import get_llm

    response = await get_llm().ainvoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    )
    return str(response.content)


def create_time_slot_rename_graph(
    *,
    fetch_slot: Callable[[str], dict[str, Any]] = notion_io.fetch_time_slot,
    fetch_task: Callable[[str], dict[str, Any]] = notion_io.fetch_task,
    fetch_examples: Callable[[], list[dict[str, str]]] = notion_io.fetch_recent_named_slots,
    update_name: Callable[[str, str], None] = notion_io.update_time_slot_name,
    propose: ProposeFn = _default_propose,
):
    async def fetch_slot_node(state: TimeSlotRenameState) -> TimeSlotRenameState:
        page_id = notion_io.parse_page_id(state["slot_url"])
        return {"slot": fetch_slot(page_id)}

    async def fetch_tasks_node(state: TimeSlotRenameState) -> TimeSlotRenameState:
        task_ids = state["slot"].get("task_ids", [])[:_MAX_TASKS]
        return {"tasks": [fetch_task(task_id) for task_id in task_ids]}

    async def gather_context_node(state: TimeSlotRenameState) -> TimeSlotRenameState:
        return {
            "examples": fetch_examples(),
            "course_short_names": naming.load_course_short_names(),
        }

    async def propose_name_node(state: TimeSlotRenameState) -> TimeSlotRenameState:
        raw = await propose(_SYSTEM_PROMPT, _build_user_prompt(state))
        name, learned = _parse_llm_response(raw)
        return {"proposed_name": name, "learned_short_names": learned}

    async def apply_update_node(state: TimeSlotRenameState) -> TimeSlotRenameState:
        slot = state["slot"]
        proposed = state["proposed_name"]
        update_name(slot["id"], proposed)
        naming.record_course_short_names(state.get("learned_short_names", {}))
        return {
            "updated": True,
            "message": f"🏷️ Time block renamed: “{slot.get('name') or '(unnamed)'}” → “{proposed}”",
        }

    async def respond_node(state: TimeSlotRenameState) -> TimeSlotRenameState:
        if state.get("message"):
            return {}
        if not state.get("slot", {}).get("task_ids"):
            return {
                "updated": False,
                "message": "Time block has no linked tasks — name left unchanged.",
            }
        return {
            "updated": False,
            "message": f"Time block name already fits its tasks: “{state['slot'].get('name')}”",
        }

    def has_tasks(state: TimeSlotRenameState) -> str:
        return "fetch_tasks" if state["slot"].get("task_ids") else "respond"

    def name_changed(state: TimeSlotRenameState) -> str:
        proposed = state.get("proposed_name", "").strip()
        current = state.get("slot", {}).get("name", "").strip()
        return "apply_update" if proposed and proposed != current else "respond"

    graph = StateGraph(TimeSlotRenameState)
    graph.add_node("fetch_slot", fetch_slot_node)
    graph.add_node("fetch_tasks", fetch_tasks_node)
    graph.add_node("gather_context", gather_context_node)
    graph.add_node("propose_name", propose_name_node)
    graph.add_node("apply_update", apply_update_node)
    graph.add_node("respond", respond_node)

    graph.set_entry_point("fetch_slot")
    graph.add_conditional_edges("fetch_slot", has_tasks, {"fetch_tasks": "fetch_tasks", "respond": "respond"})
    graph.add_edge("fetch_tasks", "gather_context")
    graph.add_edge("gather_context", "propose_name")
    graph.add_conditional_edges(
        "propose_name", name_changed, {"apply_update": "apply_update", "respond": "respond"}
    )
    graph.add_edge("apply_update", "respond")
    graph.add_edge("respond", END)
    return graph.compile()


_compiled_graph = None


async def run_time_slot_rename(url: str) -> str:
    """Entry point used by the update_time_slot_name automation."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = create_time_slot_rename_graph()

    state = await _compiled_graph.ainvoke({"slot_url": url})
    return state.get("message", "Time slot workflow finished with no message.")
