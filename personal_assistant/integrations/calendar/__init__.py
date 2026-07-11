"""Calendar integration structure.

Provider-agnostic on purpose, mirroring how the Telegram layer wraps the
communication platform: agent code should depend on CalendarProvider /
CalendarEvent from base.py and obtain a concrete provider through
factory.get_calendar_provider(), never on a specific vendor SDK.

Modules:
- base.py             CalendarEvent model + CalendarProvider interface
- google_calendar.py  Google Calendar implementation (skeleton, auth TODO)
- factory.py          picks the provider from CALENDAR_PROVIDER env var
"""

from personal_assistant.integrations.calendar.base import CalendarEvent, CalendarProvider
from personal_assistant.integrations.calendar.factory import get_calendar_provider

__all__ = ["CalendarEvent", "CalendarProvider", "get_calendar_provider"]
