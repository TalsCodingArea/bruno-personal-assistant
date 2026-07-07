from __future__ import annotations

import unittest
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import tool

from personal_assistant.agent.general.builder import GeneralAgentRuntime, _build_graph
from personal_assistant.agent.general.memory import MemoryStore


class FakeToolCallingModel:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = responses
        self.calls: list[list[BaseMessage]] = []
        self.configs: list[dict[str, Any] | None] = []

    def bind_tools(self, tools: list[Any]):
        return self

    async def ainvoke(self, messages: list[BaseMessage], config: dict[str, Any] | None = None) -> AIMessage:
        self.calls.append(messages)
        self.configs.append(config)
        return self.responses.pop(0)


@tool
def echo_tool(text: str) -> str:
    """Echo text for graph routing tests."""
    return f"echo: {text}"


class GeneralAgentGraphTest(unittest.IsolatedAsyncioTestCase):
    async def test_answers_without_tools_and_saves_memory(self) -> None:
        model = FakeToolCallingModel([AIMessage(content="direct answer"), AIMessage(content="second answer")])
        runtime = GeneralAgentRuntime(_build_graph(model, []), MemoryStore())

        result = await runtime.ainvoke(
            {"input": "hello"},
            config={"configurable": {"session_id": "chat-1"}},
        )

        self.assertEqual(result, {"output": "direct answer"})
        self.assertEqual(model.calls[-1][-1].content, "hello")
        self.assertEqual(model.configs[-1]["configurable"]["session_id"], "chat-1")

        result = await runtime.ainvoke(
            {"input": "again"},
            config={"configurable": {"session_id": "chat-1"}},
        )

        self.assertEqual(result, {"output": "second answer"})
        self.assertEqual(model.calls[-1][-3].content, "hello")
        self.assertEqual(model.calls[-1][-2].content, "direct answer")
        self.assertEqual(model.calls[-1][-1].content, "again")

    async def test_routes_tool_calls_through_tool_node(self) -> None:
        model = FakeToolCallingModel(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "echo_tool",
                            "args": {"text": "hello"},
                            "id": "call-1",
                        }
                    ],
                ),
                AIMessage(content="tool answer"),
            ]
        )
        runtime = GeneralAgentRuntime(_build_graph(model, [echo_tool]), MemoryStore())

        result = await runtime.ainvoke(
            {"input": "use a tool"},
            config={"configurable": {"session_id": "chat-1"}},
        )

        self.assertEqual(result, {"output": "tool answer"})
        self.assertTrue(any(isinstance(message, ToolMessage) for message in model.calls[-1]))


if __name__ == "__main__":
    unittest.main()
