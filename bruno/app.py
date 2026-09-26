"""Run Bruno from the repository root with `python -m bruno.app`."""

import logging
import os

from bruno.telegram_bot.bot import run_bot


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    run_bot()


if __name__ == "__main__":
    main()
