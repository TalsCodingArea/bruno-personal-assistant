from __future__ import annotations

import logging
import os

from personal_assistant.telegram.bot import run_bot


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


def main() -> None:
    run_bot()


if __name__ == "__main__":
    main()
