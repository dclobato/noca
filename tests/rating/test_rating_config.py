#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for rating configuration, next-update formatting, and the points formula."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from shared.services.arena_rating import (
    BASE_POINTS,
    GROWTH,
    _points_for_difficulty,
    format_next_rating_update,
    format_rating_interval,
)

# ---------------------------------------------------------------------------
# Config validation tests
# ---------------------------------------------------------------------------


def test_rating_interval_boundary_validation() -> None:
    """NOCA_RATING_INTERVAL must reject values outside [900, 604800]."""
    import os

    from rating.config import Settings

    os.environ["NOCA_RATING_INTERVAL"] = "899"
    try:
        with pytest.raises((ValidationError, ValueError)):
            Settings()
    finally:
        del os.environ["NOCA_RATING_INTERVAL"]

    os.environ["NOCA_RATING_INTERVAL"] = "604801"
    try:
        with pytest.raises((ValidationError, ValueError)):
            Settings()
    finally:
        del os.environ["NOCA_RATING_INTERVAL"]

    os.environ["NOCA_RATING_INTERVAL"] = "900"
    try:
        assert Settings().RATING_INTERVAL == 900
    finally:
        del os.environ["NOCA_RATING_INTERVAL"]


def test_rating_interval_default_is_valid() -> None:
    """Default NOCA_RATING_INTERVAL (86400) must pass validation."""
    import os

    for key in ("NOCA_RATING_INTERVAL", "NOCA_RATING_COMPUTE_ON_STARTUP"):
        os.environ.pop(key, None)

    from rating.config import Settings

    s = Settings(_env_file=None)
    assert s.RATING_INTERVAL == 86400
    assert s.COMPUTE_RATINGS_ON_STARTUP is False


def test_compute_ratings_on_startup_env_true() -> None:
    """NOCA_RATING_COMPUTE_ON_STARTUP=true must parse as True."""
    import os

    from rating.config import Settings

    os.environ["NOCA_RATING_COMPUTE_ON_STARTUP"] = "true"
    try:
        s = Settings()
    finally:
        del os.environ["NOCA_RATING_COMPUTE_ON_STARTUP"]

    assert s.COMPUTE_RATINGS_ON_STARTUP is True


def test_rating_presence_defaults_and_ttl_validation() -> None:
    """Use 30/60-second presence defaults and reject a non-greater TTL."""
    from rating.config import Settings

    settings = Settings(_env_file=None, RATING_WORKER_ID="")
    assert settings.RATING_WORKER_ID == ""
    assert settings.RATING_PRESENCE_INTERVAL_SECONDS == 30
    assert settings.RATING_PRESENCE_TTL_SECONDS == 60

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            RATING_WORKER_ID="",
            RATING_PRESENCE_INTERVAL_SECONDS=30,
            RATING_PRESENCE_TTL_SECONDS=30,
        )


def test_format_next_rating_update_handles_empty_and_expired_values() -> None:
    """Next rating update text should be absent or immediate for inactive deadlines."""
    assert format_next_rating_update(None) is None
    assert format_next_rating_update(datetime.now(UTC) - timedelta(seconds=5)) == "less than a minute"


def test_format_next_rating_update_uses_relative_datetime_for_future_values() -> None:
    """Future next rating update text should use relative-datetime output."""
    text = format_next_rating_update(datetime.now(UTC) + timedelta(hours=2, minutes=5))

    assert text is not None
    assert "hour" in text


def test_format_rating_interval_handles_minutes_and_compound_values() -> None:
    """Rating interval text should support sub-hour and compound intervals."""
    assert format_rating_interval(None) is None
    assert format_rating_interval(0) is None
    assert format_rating_interval(900) == "15 minutes"
    assert format_rating_interval(3661) == "1 hour, 1 minute and 1 second"


# ---------------------------------------------------------------------------
# Points formula test
# ---------------------------------------------------------------------------


def test_points_for_difficulty_growth() -> None:
    """Higher difficulty must yield strictly more points; boundaries are in range."""
    # Test 10 representative values across the new [1, 100] internal scale
    test_difficulties = list(range(1, 101, 11))  # [1, 12, 23, 34, 45, 56, 67, 78, 89, 100]
    points = [_points_for_difficulty(d) for d in test_difficulties]
    for i in range(len(points) - 1):
        assert points[i] < points[i + 1], f"Non-monotonic at difficulty {test_difficulties[i]}"
    # difficulty=1 → GROWTH^0 = BASE_POINTS; difficulty=100 → GROWTH^(99/11) = GROWTH^9
    assert abs(points[0] - BASE_POINTS) < 1e-9
    assert abs(points[-1] - BASE_POINTS * (GROWTH**9)) < 1e-9
