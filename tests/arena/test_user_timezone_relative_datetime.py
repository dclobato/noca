#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena relative datetime formatting."""

from datetime import UTC, datetime, timedelta

from arena.services.user_timezone_service import format_relative_datetime


def test_format_relative_datetime_formats_past_values() -> None:
    """Append an ago suffix to past durations."""
    value = datetime.now(UTC) - timedelta(minutes=5)

    assert format_relative_datetime(value) == "5 minutes ago"


def test_format_relative_datetime_handles_none() -> None:
    """Return the configured fallback for a missing datetime."""
    assert format_relative_datetime(None, fallback="unknown") == "unknown"
