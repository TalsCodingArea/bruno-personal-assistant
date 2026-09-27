"""Platform-neutral streaming events adapted from Bruno's Telegram workflow."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import ChatGenerationChunk, GenerationChunk

AgentEventType = Literal[
    "processing",
    "tool_calling",
    "generating_response",
    "response_delta",
    "done",
    "approval",
    "error",
]


@dataclass(frozen=True, slots=True)
class AgentEvent:
    type: AgentEventType
    message: str | None = None
    tool_name: str | None = None
    content_delta: str | None = None
    output: dict[str, Any] | None = None
    error: str | None = None


class AgentEventCallback(AsyncCallbackHandler):
    def __init__(self, queue: asyncio.Queue[AgentEvent]) -> None:
        self._queue = queue

    async def on_chat_model_start(self, *_: Any, **__: Any) -> None:
        self._queue.put_nowait(
            AgentEvent(type="generating_response", message="Generating response")
        )

    async def on_llm_new_token(
        self,
        token: str | list[str | dict[str, Any]],
        *,
        chunk: GenerationChunk | ChatGenerationChunk | None = None,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        if isinstance(token, str) and token:
            self._queue.put_nowait(
                AgentEvent(type="response_delta", content_delta=token)
            )

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._queue.put_nowait(
            AgentEvent(
                type="tool_calling",
                message="Tool calling",
                tool_name=str(serialized.get("name") or kwargs.get("name") or ""),
            )
        )

    async def on_tool_end(self, *_: Any, **__: Any) -> None:
        self._queue.put_nowait(
            AgentEvent(type="processing", message="Processing tool result")
        )


async def stream_agent_events(
    run: Any,
) -> AsyncIterator[AgentEvent]:
    """Run a callback-aware capability coroutine and stream its lifecycle."""

    queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
    callback = AgentEventCallback(queue)
    yield AgentEvent(type="processing", message="Processing")
    task = asyncio.create_task(run([callback]))
    while not task.done() or not queue.empty():
        try:
            yield await asyncio.wait_for(queue.get(), timeout=0.1)
        except TimeoutError:
            continue
    try:
        output = task.result()
    except Exception as exc:
        yield AgentEvent(type="error", message="Capability run failed", error=str(exc))
        return
    if output.get("approval_request") is not None:
        yield AgentEvent(type="approval", output=output)
    else:
        yield AgentEvent(type="done", message="Done", output=output)
