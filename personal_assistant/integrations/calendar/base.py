from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class CalendarEvent:
    """Provider-agnostic calendar event.

    Every provider maps its own payload into this shape, so nothing outside
    this package ever handles vendor-specific event formats.
    """

    event_id: str
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    location: str | None = None
    description: str | None = None
    attendees: list[str] = field(default_factory=list)
    calendar_id: str = "primary"
    provider: str = "unknown"


class CalendarProvider(ABC):
    """Interface every calendar backend must implement.

    Read-only for now -- pulling events is the current need. Write operations
    (create/update events) should be added here first, then implemented per
    provider, when a feature actually needs them.
    """

    name: str = "unknown"

    @abstractmethod
    def list_calendars(self) -> list[dict]:
        """Return available calendars as [{"id": ..., "name": ...}, ...]."""

    @abstractmethod
    def get_events(
        self,
        start: datetime,
        end: datetime,
        calendar_id: str = "primary",
    ) -> list[CalendarEvent]:
        """Return events overlapping [start, end), sorted by start time."""
