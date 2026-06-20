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

UTC_TIMEZONE_NAME = "UTC"

_COUNTRY_DEFAULT_TIMEZONES: dict[str, str] = {
    "AU": "Australia/Sydney",
    "BR": "America/Sao_Paulo",
    "CA": "America/Toronto",
    "ES": "Europe/Madrid",
    "MX": "America/Mexico_City",
    "NZ": "Pacific/Auckland",
    "PT": "Europe/Lisbon",
    "RU": "Europe/Moscow",
    "US": "America/New_York",
}

_SUBDIVISION_TIMEZONES: dict[str, str] = {
    # Brazil
    "BR-AC": "America/Rio_Branco",
    "BR-AL": "America/Maceio",
    "BR-AM": "America/Manaus",
    "BR-AP": "America/Belem",
    "BR-BA": "America/Bahia",
    "BR-CE": "America/Fortaleza",
    "BR-DF": "America/Sao_Paulo",
    "BR-ES": "America/Sao_Paulo",
    "BR-GO": "America/Sao_Paulo",
    "BR-MA": "America/Fortaleza",
    "BR-MG": "America/Sao_Paulo",
    "BR-MS": "America/Campo_Grande",
    "BR-MT": "America/Cuiaba",
    "BR-PA": "America/Belem",
    "BR-PB": "America/Fortaleza",
    "BR-PE": "America/Recife",
    "BR-PI": "America/Fortaleza",
    "BR-PR": "America/Sao_Paulo",
    "BR-RJ": "America/Sao_Paulo",
    "BR-RN": "America/Fortaleza",
    "BR-RO": "America/Porto_Velho",
    "BR-RR": "America/Boa_Vista",
    "BR-RS": "America/Sao_Paulo",
    "BR-SC": "America/Sao_Paulo",
    "BR-SE": "America/Maceio",
    "BR-SP": "America/Sao_Paulo",
    "BR-TO": "America/Araguaina",
    # United States
    "US-AK": "America/Anchorage",
    "US-AL": "America/Chicago",
    "US-AR": "America/Chicago",
    "US-AZ": "America/Phoenix",
    "US-CA": "America/Los_Angeles",
    "US-CO": "America/Denver",
    "US-CT": "America/New_York",
    "US-DC": "America/New_York",
    "US-DE": "America/New_York",
    "US-FL": "America/New_York",
    "US-GA": "America/New_York",
    "US-HI": "Pacific/Honolulu",
    "US-IA": "America/Chicago",
    "US-ID": "America/Boise",
    "US-IL": "America/Chicago",
    "US-IN": "America/Indiana/Indianapolis",
    "US-KS": "America/Chicago",
    "US-KY": "America/New_York",
    "US-LA": "America/Chicago",
    "US-MA": "America/New_York",
    "US-MD": "America/New_York",
    "US-ME": "America/New_York",
    "US-MI": "America/Detroit",
    "US-MN": "America/Chicago",
    "US-MO": "America/Chicago",
    "US-MS": "America/Chicago",
    "US-MT": "America/Denver",
    "US-NC": "America/New_York",
    "US-ND": "America/Chicago",
    "US-NE": "America/Chicago",
    "US-NH": "America/New_York",
    "US-NJ": "America/New_York",
    "US-NM": "America/Denver",
    "US-NV": "America/Los_Angeles",
    "US-NY": "America/New_York",
    "US-OH": "America/New_York",
    "US-OK": "America/Chicago",
    "US-OR": "America/Los_Angeles",
    "US-PA": "America/New_York",
    "US-RI": "America/New_York",
    "US-SC": "America/New_York",
    "US-SD": "America/Chicago",
    "US-TN": "America/Chicago",
    "US-TX": "America/Chicago",
    "US-UT": "America/Denver",
    "US-VA": "America/New_York",
    "US-VT": "America/New_York",
    "US-WA": "America/Los_Angeles",
    "US-WI": "America/Chicago",
    "US-WV": "America/New_York",
    "US-WY": "America/Denver",
    # Canada
    "CA-AB": "America/Edmonton",
    "CA-BC": "America/Vancouver",
    "CA-MB": "America/Winnipeg",
    "CA-NB": "America/Moncton",
    "CA-NL": "America/St_Johns",
    "CA-NS": "America/Halifax",
    "CA-NT": "America/Yellowknife",
    "CA-NU": "America/Iqaluit",
    "CA-ON": "America/Toronto",
    "CA-PE": "America/Halifax",
    "CA-QC": "America/Toronto",
    "CA-SK": "America/Regina",
    "CA-YT": "America/Whitehorse",
    # Australia
    "AU-ACT": "Australia/Sydney",
    "AU-NSW": "Australia/Sydney",
    "AU-NT": "Australia/Darwin",
    "AU-QLD": "Australia/Brisbane",
    "AU-SA": "Australia/Adelaide",
    "AU-TAS": "Australia/Hobart",
    "AU-VIC": "Australia/Melbourne",
    "AU-WA": "Australia/Perth",
    # Portugal
    "PT-20": "Atlantic/Azores",
    "PT-30": "Atlantic/Madeira",
}


def timezone_name_for_user(user: Any | None) -> str:
    """Return the display timezone name derived from an Arena user's location."""
    country_code = _normalize_code(getattr(user, "country_code", None))
    subdivision_code = _normalize_code(getattr(user, "subdivision_code", None))
    if subdivision_code and subdivision_code in _SUBDIVISION_TIMEZONES:
        return _SUBDIVISION_TIMEZONES[subdivision_code]
    if country_code is None:
        return UTC_TIMEZONE_NAME
    if country_code in _COUNTRY_DEFAULT_TIMEZONES:
        return _COUNTRY_DEFAULT_TIMEZONES[country_code]
    try:
        country_timezones = pytz.country_timezones(country_code)
    except KeyError, AttributeError:
        return UTC_TIMEZONE_NAME
    return country_timezones[0] if country_timezones else UTC_TIMEZONE_NAME


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


def _normalize_code(value: Any | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().upper()
