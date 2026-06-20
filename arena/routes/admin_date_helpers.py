#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared date / pagination helpers reused by Arena admin route modules.

Extracted from admin_dashboard.py and admin_users.py to avoid duplication.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import cast

import pytz


def _parse_date_param(value: str | None) -> date | None:
    """Parse a YYYY-MM-DD string; return None for blank or invalid input.

    Args:
        value: Raw query-parameter string.

    Returns:
        date: Parsed date, or None when the value is absent or malformed.
    """
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _local_midnight_to_utc(d: date, tz_name: str) -> datetime:
    """Return the UTC datetime corresponding to midnight of *d* in *tz_name*.

    Args:
        d: Local calendar date.
        tz_name: IANA timezone name (e.g. ``"America/Sao_Paulo"``).

    Returns:
        datetime: UTC datetime for midnight on that date in the given timezone.
    """
    tz = pytz.timezone(tz_name)
    local_midnight = datetime(d.year, d.month, d.day)
    try:
        localized = tz.localize(local_midnight, is_dst=None)
    except pytz.AmbiguousTimeError, pytz.NonExistentTimeError:
        localized = tz.localize(local_midnight, is_dst=False)
    return cast(datetime, localized.astimezone(UTC))


def _effective_per_page(value: str | None, *, allowed: set[int], default: int) -> int:
    """Return a validated page size from *value*, falling back to *default*.

    Args:
        value: Raw query-parameter string.
        allowed: Set of acceptable integer values.
        default: Value to use when *value* is absent, invalid, or not in *allowed*.

    Returns:
        int: A member of *allowed*, or *default*.
    """
    try:
        resolved = int(value) if value else default
    except TypeError, ValueError:
        return default
    return resolved if resolved in allowed else default
