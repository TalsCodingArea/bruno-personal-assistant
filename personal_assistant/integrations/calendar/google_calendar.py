"""Google Calendar provider -- skeleton.

Implementation plan (when this feature gets prioritized):

1. Create a Google Cloud project, enable the Calendar API, and create OAuth
   desktop-app credentials. Save the client secret file locally and point
   GOOGLE_CALENDAR_CREDENTIALS_PATH at it.
2. Add dependencies: google-api-python-client, google-auth-oauthlib.
3. First run performs the OAuth consent flow in a browser (fine on the Mac
   Mini) and stores the refresh token at GOOGLE_CALENDAR_TOKEN_PATH so later
   runs are headless.
4. Fill in the two methods below using the events().list / calendarList()
   endpoints and map responses to CalendarEvent.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from personal_assistant.integrations.calendar.base import CalendarEvent, CalendarProvider

_DEFAULT_CREDENTIALS_PATH = Path(__file__).resolve().parents[3] / "budget_data" / "google_calendar_credentials.json"
_DEFAULT_TOKEN_PATH = Path(__file__).resolve().parents[3] / "budget_data" / "google_calendar_token.json"


class GoogleCalendarProvider(CalendarProvider):
    name = "google"

    def __init__(self) -> None:
        self._credentials_path = Path(
            os.getenv("GOOGLE_CALENDAR_CREDENTIALS_PATH", str(_DEFAULT_CREDENTIALS_PATH))
        )
        self._token_path = Path(os.getenv("GOOGLE_CALENDAR_TOKEN_PATH", str(_DEFAULT_TOKEN_PATH)))

    def _build_service(self):
        """Authenticate and return the Google Calendar API service object."""
        raise NotImplementedError(
            "Google Calendar auth is not implemented yet. See the module "
            "docstring for the implementation plan."
        )

    def list_calendars(self) -> list[dict]:
        raise NotImplementedError("Google Calendar integration is not implemented yet.")

    def get_events(
        self,
        start: datetime,
        end: datetime,
        calendar_id: str = "primary",
    ) -> list[CalendarEvent]:
        raise NotImplementedError("Google Calendar integration is not implemented yet.")

    @staticmethod
    def _to_calendar_event(raw: dict, calendar_id: str) -> CalendarEvent:
        """Map a Google Calendar API event resource to CalendarEvent.

        Kept separate (and static) so it can be unit-tested with recorded
        payloads before the auth plumbing exists.
        """
        start_info = raw.get("start", {})
        end_info = raw.get("end", {})
        all_day = "date" in start_info

        def _parse(info: dict) -> datetime:
            value = info.get("dateTime") or info.get("date") or ""
            return datetime.fromisoformat(value.replace("Z", "+00:00"))

        return CalendarEvent(
            event_id=str(raw.get("id", "")),
            title=str(raw.get("summary", "(no title)")),
            start=_parse(start_info),
            end=_parse(end_info),
            all_day=all_day,
            location=raw.get("location"),
            description=raw.get("description"),
            attendees=[
                attendee.get("email", "")
                for attendee in raw.get("attendees", [])
                if attendee.get("email")
            ],
            calendar_id=calendar_id,
            provider="google",
        )
