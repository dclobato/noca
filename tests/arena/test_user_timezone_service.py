#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena user-location timezone helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from arena.services.user_timezone_service import (
    format_user_datetime,
    parse_user_datetime_local,
    timezone_name_for_user,
    to_user_timezone,
)


@dataclass(frozen=True)
class _UserLocation:
    country_code: str | None
    subdivision_code: str | None


def test_timezone_name_for_user_falls_back_to_utc_without_location() -> None:
    """Users without a saved location use UTC."""
    user = _UserLocation(country_code=None, subdivision_code=None)

    assert timezone_name_for_user(user) == "UTC"


def test_timezone_name_for_user_uses_single_zone_country() -> None:
    """Single-zone countries resolve through pytz country timezones."""
    user = _UserLocation(country_code="JP", subdivision_code=None)

    assert timezone_name_for_user(user) == "Asia/Tokyo"


def test_timezone_name_for_user_prefers_subdivision_mapping() -> None:
    """Subdivision mappings override broad country defaults."""
    user = _UserLocation(country_code="BR", subdivision_code="BR-SP")

    assert timezone_name_for_user(user) == "America/Sao_Paulo"


def test_format_user_datetime_converts_utc_to_user_timezone() -> None:
    """UTC datetimes are rendered in the user's derived timezone."""
    user = _UserLocation(country_code="BR", subdivision_code="BR-SP")
    value = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)

    assert format_user_datetime(value, user) == "2026-06-04 09:00 -03"


def test_to_user_timezone_treats_naive_datetime_as_utc() -> None:
    """Naive backend datetimes are interpreted as UTC."""
    user = _UserLocation(country_code="JP", subdivision_code=None)
    value = datetime(2026, 6, 4, 12, 0)

    converted = to_user_timezone(value, user)

    assert converted is not None
    assert converted.strftime("%Y-%m-%d %H:%M %Z") == "2026-06-04 21:00 JST"


def test_parse_user_datetime_local_returns_utc() -> None:
    """Browser local datetime input is converted back to UTC."""
    user = _UserLocation(country_code="BR", subdivision_code="BR-SP")

    parsed = parse_user_datetime_local("2026-06-04T09:00", user)

    assert parsed == datetime(2026, 6, 4, 12, 0, tzinfo=UTC)


def test_parse_user_datetime_local_rejects_nonexistent_dst_time() -> None:
    """Nonexistent local wall times fail validation."""
    user = _UserLocation(country_code="US", subdivision_code="US-NY")

    with pytest.raises(ValueError, match="does not exist"):
        parse_user_datetime_local("2026-03-08T02:30", user)
