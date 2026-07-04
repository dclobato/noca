#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Timezone helpers for Arena user-facing date/time rendering."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytz
from relative_datetime import DateTimeUtils

from shared.services.user_timezone import timezone_name_for_country


def timezone_name_for_user(user: Any | None) -> str:
    """Return the display timezone name derived from an Arena user's location."""
    return timezone_name_for_country(
        getattr(user, "country_code", None),
        getattr(user, "subdivision_code", None),
    )


def to_user_timezone(value: datetime | None, user: Any | None) -> datetime | None:
    """Convert a UTC datetime to the timezone derived from a user's location."""
    if value is None:
        return None
    source = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    timezone = pytz.timezone(timezone_name_for_user(user))
    return source.astimezone(timezone)


def format_user_datetime(
    value: datetime | None,
    user: Any | None,
    fmt: str = "%Y-%m-%d %H:%M %Z",
    *,
    fallback: str = "-",
) -> str:
    """Format a UTC datetime in the user's derived timezone."""
    converted = to_user_timezone(value, user)
    return fallback if converted is None else converted.strftime(fmt)


def format_relative_datetime(value: datetime | None, *, fallback: str = "-") -> str:
    """Format a datetime as a human-readable duration relative to now."""
    if value is None:
        return fallback
    source = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    relative_time, direction = DateTimeUtils.relative_datetime(source.astimezone(UTC))
    if direction == "past":
        return f"{relative_time} ago"
    if direction == "future":
        return f"in {relative_time}"
    return str(relative_time)


def datetime_local_value(value: datetime | None, user: Any | None) -> str:
    """Format a UTC datetime for a browser ``datetime-local`` input."""
    converted = to_user_timezone(value, user)
    return "" if converted is None else converted.strftime("%Y-%m-%dT%H:%M")


def parse_user_datetime_local(value: str, user: Any | None) -> datetime | None:
    """Parse a browser ``datetime-local`` value in the user's timezone as UTC."""
    clean = value.strip()
    if not clean:
        return None
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError as exc:
        raise ValueError("Invalid date/time.") from exc
    timezone = pytz.timezone(timezone_name_for_user(user))
    if parsed.tzinfo is None:
        try:
            localized = timezone.localize(parsed, is_dst=None)
        except pytz.AmbiguousTimeError as exc:
            raise ValueError("The selected date/time is ambiguous in your timezone.") from exc
        except pytz.NonExistentTimeError as exc:
            raise ValueError("The selected date/time does not exist in your timezone.") from exc
    else:
        localized = parsed.astimezone(timezone)
    return cast(datetime, localized.astimezone(pytz.utc))
