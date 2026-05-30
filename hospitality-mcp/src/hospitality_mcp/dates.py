"""Flexible date parsing so tools accept natural inputs from an LLM.

Supported forms (case-insensitive):
  - "today", "tomorrow", "yesterday"
  - weekday names: "monday" ... "sunday" (the next occurrence, today included)
  - ISO dates: "2026-05-31"
  - relative offsets: "+2" / "-1" (days from today)
  - empty / None -> defaults to today
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


class DateParseError(ValueError):
    """Raised when a day string cannot be understood."""


def parse_day(value: Optional[str], *, today: Optional[date] = None) -> date:
    today = today or date.today()
    if value is None:
        return today

    text = value.strip().lower()
    if not text or text == "today":
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    if text == "yesterday":
        return today - timedelta(days=1)

    if text in _WEEKDAYS:
        target = _WEEKDAYS[text]
        delta = (target - today.weekday()) % 7
        return today + timedelta(days=delta)

    if text[0] in "+-" and text[1:].isdigit():
        return today + timedelta(days=int(text))

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    raise DateParseError(
        f"Could not parse day {value!r}. Use 'today', 'tomorrow', a weekday, "
        f"an ISO date like 2026-05-31, or an offset like '+2'."
    )
