from __future__ import annotations

import unittest
from typing import Any

from agent.workflow import stream_agent_events


class FakeAgent:
    async def ainvoke(self, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        callbacks = config["callbacks"]
        for callback in callbacks:
            await callback.on_chat_model_start({}, [])
            await callback.on_llm_new_token("hello")
            await callback.on_llm_new_token(" world")
            await callback.on_tool_start({"name": "web_search"}, "")
            await callback.on_tool_end("search results")
        return {"output": f"answer to {inputs['input']}"}


class AgentWorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def test_streams_neutral_agent_events(self) -> None:
        events = [
            event
            async for event in stream_agent_events(
                FakeAgent(),
                session_id="session-1",
                user_text="hello",
            )
        ]

        self.assertEqual(events[0].type, "processing")
        self.assertIn("generating_response", [event.type for event in events])
        self.assertEqual(
            "".join(event.content_delta or "" for event in events if event.type == "response_delta"),
            "hello world",
        )
        self.assertIn("tool_calling", [event.type for event in events])
        self.assertEqual(events[-1].type, "done")
        self.assertEqual(events[-1].output, {"output": "answer to hello"})


if __name__ == "__main__":
    unittest.main()
