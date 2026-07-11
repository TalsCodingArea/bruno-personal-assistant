from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from personal_assistant.agent.capabilities.time_slots import naming
from personal_assistant.agent.capabilities.time_slots.graph import (
    _parse_llm_response,
    create_time_slot_rename_graph,
)
from personal_assistant.agent.capabilities.time_slots.notion_io import parse_page_id


class ParsePageIdTest(unittest.TestCase):
    def test_parses_common_url_forms(self) -> None:
        expected = "12345678-90ab-cdef-1234-567890abcdef"
        for url in [
            "https://www.notion.so/My-Slot-1234567890abcdef1234567890abcdef",
            "https://app.notion.com/p/1234567890abcdef1234567890abcdef?pvs=1",
            "12345678-90ab-cdef-1234-567890abcdef",
            "1234567890abcdef1234567890abcdef",
        ]:
            self.assertEqual(parse_page_id(url), expected, url)

    def test_rejects_urls_without_page_id(self) -> None:
        with self.assertRaises(ValueError):
            parse_page_id("https://www.notion.so/whatever")


class CourseShortNameStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"TIME_SLOTS_DATA_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_records_and_loads_pairs(self) -> None:
        naming.record_course_short_names({"Numeric Analysis": "Numeric"})
        self.assertEqual(naming.load_course_short_names(), {"Numeric Analysis": "Numeric"})

    def test_existing_short_names_win(self) -> None:
        naming.record_course_short_names({"Numeric Analysis": "Numeric"})
        naming.record_course_short_names({"Numeric Analysis": "NumAn", "Data Structures": "Data"})
        self.assertEqual(
            naming.load_course_short_names(),
            {"Numeric Analysis": "Numeric", "Data Structures": "Data"},
        )


class ParseLlmResponseTest(unittest.TestCase):
    def test_parses_plain_and_fenced_json(self) -> None:
        payload = '{"name": "Numeric - Ex.5 Q1-Q3", "course_short_names": {"Numeric Analysis": "Numeric"}}'
        for raw in [payload, f"```json\n{payload}\n```"]:
            name, short_names = _parse_llm_response(raw)
            self.assertEqual(name, "Numeric - Ex.5 Q1-Q3")
            self.assertEqual(short_names, {"Numeric Analysis": "Numeric"})

    def test_rejects_empty_name(self) -> None:
        with self.assertRaises(ValueError):
            _parse_llm_response('{"name": "", "course_short_names": {}}')


def _slot(task_ids: list[str], name: str = "Time Slots") -> dict:
    return {
        "id": "slot-1",
        "name": name,
        "tasks_text": "",
        "task_ids": task_ids,
        "date": "2026-07-14T12:00:00.000Z",
    }


class TimeSlotRenameGraphTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"TIME_SLOTS_DATA_DIR": self._tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_renames_slot_and_learns_short_names(self) -> None:
        updates: list[tuple[str, str]] = []
        prompts: list[str] = []

        async def propose(system_prompt: str, user_prompt: str) -> str:
            prompts.append(user_prompt)
            return json.dumps(
                {
                    "name": "Numeric - Ex.5 Q1-Q3",
                    "course_short_names": {"Numeric Analysis": "Numeric"},
                }
            )

        graph = create_time_slot_rename_graph(
            fetch_slot=lambda page_id: _slot(["t1", "t2"]),
            fetch_task=lambda task_id: {
                "name": f"Ex.5 - Q{task_id[-1]}",
                "status": "Not started",
                "course": "Numeric Analysis",
            },
            fetch_examples=lambda: [{"name": "Numeric - Ex.4 Q1-Q4", "tasks": "Ex.4 - Q1,Ex.4 - Q2"}],
            update_name=lambda page_id, new_name: updates.append((page_id, new_name)),
            propose=propose,
        )

        state = await graph.ainvoke(
            {"slot_url": "https://app.notion.com/p/1234567890abcdef1234567890abcdef"}
        )

        self.assertTrue(state["updated"])
        self.assertEqual(updates, [("slot-1", "Numeric - Ex.5 Q1-Q3")])
        self.assertIn("Numeric - Ex.5 Q1-Q3", state["message"])
        self.assertEqual(naming.load_course_short_names(), {"Numeric Analysis": "Numeric"})
        # The LLM saw the tasks, their course, and the style examples.
        self.assertIn("Numeric Analysis", prompts[0])
        self.assertIn("Numeric - Ex.4 Q1-Q4", prompts[0])

    async def test_skips_update_when_name_already_fits(self) -> None:
        updates: list[tuple[str, str]] = []

        async def propose(system_prompt: str, user_prompt: str) -> str:
            return json.dumps({"name": "Numeric - Ex.5 Q1-Q3", "course_short_names": {}})

        graph = create_time_slot_rename_graph(
            fetch_slot=lambda page_id: _slot(["t1"], name="Numeric - Ex.5 Q1-Q3"),
            fetch_task=lambda task_id: {"name": "Ex.5 - Q1", "status": "Doing", "course": "Numeric Analysis"},
            fetch_examples=lambda: [],
            update_name=lambda page_id, new_name: updates.append((page_id, new_name)),
            propose=propose,
        )

        state = await graph.ainvoke(
            {"slot_url": "https://app.notion.com/p/1234567890abcdef1234567890abcdef"}
        )

        self.assertFalse(state["updated"])
        self.assertEqual(updates, [])
        self.assertIn("already fits", state["message"])

    async def test_slot_without_tasks_exits_before_llm(self) -> None:
        llm_calls: list[str] = []

        async def propose(system_prompt: str, user_prompt: str) -> str:
            llm_calls.append(user_prompt)
            return "{}"

        graph = create_time_slot_rename_graph(
            fetch_slot=lambda page_id: _slot([]),
            fetch_task=lambda task_id: self.fail("should not fetch tasks"),
            fetch_examples=lambda: self.fail("should not fetch examples"),
            update_name=lambda page_id, new_name: self.fail("should not update"),
            propose=propose,
        )

        state = await graph.ainvoke(
            {"slot_url": "https://app.notion.com/p/1234567890abcdef1234567890abcdef"}
        )

        self.assertFalse(state["updated"])
        self.assertEqual(llm_calls, [])
        self.assertIn("no linked tasks", state["message"])


if __name__ == "__main__":
    unittest.main()
