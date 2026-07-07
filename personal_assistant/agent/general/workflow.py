from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, Sequence

from langchain_core.callbacks import AsyncCallbackHandler


AgentEventType = Literal[
    "processing",
    "tool_calling",
    "generating_response",
    "response_delta",
    "done",
    "error",
]


@dataclass(frozen=True)
class AgentEvent:
    """Platform-neutral event emitted while the assistant handles one turn."""

    type: AgentEventType
    message: str | None = None
    tool_name: str | None = None
    content_delta: str | None = None
    output: dict[str, Any] | None = None
    error: str | None = None


class AgentEventCallback(AsyncCallbackHandler):
    """Bridge LangChain lifecycle callbacks into neutral assistant events."""

    def __init__(self, queue: asyncio.Queue[AgentEvent]) -> None:
        self._queue = queue

    def _emit(self, event: AgentEvent) -> None:
        self._queue.put_nowait(event)

    async def on_chat_model_start(self, serialized: dict, messages: list[list[Any]], **kwargs: Any) -> None:
        self._emit(AgentEvent(type="generating_response", message="Generating response"))

    async def on_llm_start(self, serialized: dict, prompts: list[str], **kwargs: Any) -> None:
        self._emit(AgentEvent(type="generating_response", message="Generating response"))

    async def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        if token:
            self._emit(AgentEvent(type="response_delta", content_delta=token))

    async def on_tool_start(self, serialized: dict, input_str: str, **kwargs: Any) -> None:
        tool_name = serialized.get("name") or kwargs.get("name") or ""
        self._emit(
            AgentEvent(
                type="tool_calling",
                message="Tool calling",
                tool_name=tool_name,
            )
        )

    async def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        self._emit(AgentEvent(type="processing", message="Processing tool result"))


async def stream_agent_events(
    agent: Any,
    *,
    session_id: str,
    user_text: str,
    callbacks: Sequence[AsyncCallbackHandler] | None = None,
) -> AsyncIterator[AgentEvent]:
    """Run the assistant for one user turn and stream neutral lifecycle events."""

    queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
    event_callback = AgentEventCallback(queue)
    all_callbacks = [event_callback, *(callbacks or [])]

    async def _run_agent() -> dict[str, Any]:
        return await agent.ainvoke(
            {"input": user_text},
            config={
                "configurable": {"session_id": session_id},
                "callbacks": all_callbacks,
            },
        )

    yield AgentEvent(type="processing", message="Processing")
    task = asyncio.create_task(_run_agent())

    while True:
        if task.done() and queue.empty():
            break
        try:
            event = await asyncio.wait_for(queue.get(), timeout=0.1)
        except asyncio.TimeoutError:
            continue
        yield event

    try:
        output = task.result()
    except Exception as exc:
        yield AgentEvent(type="error", message="Agent run failed", error=str(exc))
        return

    yield AgentEvent(type="done", message="Done", output=output)
