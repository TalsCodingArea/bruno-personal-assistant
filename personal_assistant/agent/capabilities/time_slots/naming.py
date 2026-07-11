"""Course short-name memory.

Tal's slot names use shortened course names ("Numeric Analysis" -> "Numeric",
"Data Structures" -> "Data"). Rather than hard-coding the mapping, it's
learned: every time the LLM names a slot it also reports which short names it
used for which courses, and those pairs are persisted here. On later runs the
stored mapping is passed to the LLM as authoritative, which keeps naming
consistent across semesters and new courses.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("personal-assistant.time-slots")

_DEFAULT_PATH = Path(__file__).resolve().parents[4] / "budget_data" / "time_slots" / "course_short_names.json"


def _store_path() -> Path:
    override = os.getenv("TIME_SLOTS_DATA_DIR")
    path = (Path(override) / "course_short_names.json") if override else _DEFAULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_course_short_names() -> dict[str, str]:
    path = _store_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read course short names from %s", path)
        return {}


def record_course_short_names(new_pairs: dict[str, str]) -> None:
    """Merge newly observed full-name -> short-name pairs into the store.

    Existing entries win: once a short name is established, a later LLM run
    can't silently rename the course.
    """
    if not new_pairs:
        return
    current = load_course_short_names()
    added = {
        full: short
        for full, short in new_pairs.items()
        if full and short and full not in current
    }
    if not added:
        return
    current.update(added)
    _store_path().write_text(
        json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    logger.info("Learned course short names: %s", added)
