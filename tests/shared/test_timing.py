#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for shared timing utilities."""

import pytest

from shared.timing import format_compact_duration, icpc_minutes_from_seconds


@pytest.mark.parametrize(
    ("total_seconds", "expected"),
    [
        (0, "0s"),
        (10, "10s"),
        (59, "59s"),
        (60, "60s"),
        (61, "1m01s"),
        (248, "4m08s"),
        (3599, "59m59s"),
        (3600, "60m00s"),
        (3601, "1h00m"),
        (3780, "1h03m"),
        (7385, "2h03m"),
    ],
)
def test_format_compact_duration_uses_adaptive_units(total_seconds: int, expected: str) -> None:
    """Compact durations switch units at minute and hour boundaries."""
    assert format_compact_duration(total_seconds) == expected


def test_format_compact_duration_rejects_negative_values() -> None:
    """Negative elapsed durations are invalid."""
    with pytest.raises(ValueError, match="cannot be negative"):
        format_compact_duration(-1)


@pytest.mark.parametrize(
    ("timestamp_seconds", "expected"),
    [
        (0, 0),
        (59, 0),
        (60, 1),
        (90, 1),
        # 91 s used to round up to 2. Truncation is the ICPC rule.
        (91, 1),
        (119, 1),
        (3600, 60),
        # 60 min 45 s scores 60 penalty minutes, not 61.
        (3645, 60),
        (3659, 60),
    ],
)
def test_icpc_minutes_from_seconds_truncates(timestamp_seconds: int, expected: int) -> None:
    """Scoring minutes are truncated rather than rounded to the nearest minute."""
    assert icpc_minutes_from_seconds(timestamp_seconds) == expected


def test_icpc_minutes_from_seconds_passes_through_none() -> None:
    """A missing offset stays missing rather than becoming zero."""
    assert icpc_minutes_from_seconds(None) is None
