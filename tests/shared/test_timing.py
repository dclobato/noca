#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for shared timing utilities."""

import pytest

from shared.timing import format_compact_duration


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
