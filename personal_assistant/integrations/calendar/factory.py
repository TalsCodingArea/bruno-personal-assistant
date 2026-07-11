from __future__ import annotations

import os

from personal_assistant.integrations.calendar.base import CalendarProvider


def get_calendar_provider() -> CalendarProvider:
    """Return the configured calendar backend (CALENDAR_PROVIDER env var).

    Agent code should call this instead of instantiating providers directly,
    so swapping Google for CalDAV (or anything else) is a config change.
    """
    provider_name = os.getenv("CALENDAR_PROVIDER", "google").strip().lower()

    if provider_name == "google":
        from personal_assistant.integrations.calendar.google_calendar import GoogleCalendarProvider

        return GoogleCalendarProvider()

    raise ValueError(
        f"Unknown CALENDAR_PROVIDER '{provider_name}'. Supported providers: google."
    )
