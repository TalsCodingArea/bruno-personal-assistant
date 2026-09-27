"""Process-local trailing-edge debounce for post-expense check-ups."""

import asyncio
from collections.abc import Awaitable, Callable


class CheckupDebouncer:
    """Run only the latest scheduled check after a quiet period."""

    def __init__(
        self,
        delay_seconds: float,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.delay_seconds = delay_seconds
        self._sleep = sleep
        self._generation: dict[str, int] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def schedule(self, key: str, callback: Callable[[], Awaitable[None]]) -> None:
        generation = self._generation.get(key, 0) + 1
        self._generation[key] = generation

        async def run() -> None:
            await self._sleep(self.delay_seconds)
            if self._generation.get(key) != generation:
                return
            await callback()

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def aclose(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
