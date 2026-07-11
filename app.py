from __future__ import annotations

import logging
import os

from personal_assistant.telegram.bot import run_bot


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


def main() -> None:
    # First boot on a fresh container trains the expense categorizer from
    # Notion; afterwards the persisted model in budget_data/ml/ is reused.
    from personal_assistant.ml.expense_categorizer.service import ensure_model_trained

    ensure_model_trained()
    run_bot()


if __name__ == "__main__":
    main()
