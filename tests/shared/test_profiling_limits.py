#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The shared arithmetic behind per-run time limits.

``profiled_time_limit_ms`` turns an Auto-Limit observation into a stored limit;
``ceil_div`` is the rounding every legacy conversion shares.
"""

from __future__ import annotations

import math

import pytest

from shared.profiling_limits import ceil_div, profiled_time_limit_ms


def test_the_safety_factor_applies_to_the_mean_of_one_run() -> None:
    """3000 ms observed across three repetitions is a 1000 ms mean, then x1.5."""
    assert profiled_time_limit_ms(safety_factor=1.5, total_wall_time_ms=3000, repetitions=3) == 1500


def test_rounding_happens_once() -> None:
    """Dividing before the ceil would round twice and inflate the limit.

    1000 / 3 is 333.33..., which a separate ceil turns into 334 and then 501
    after the safety factor. Doing the division inside the single ceil gives
    500, which is the number the observation actually supports.
    """
    assert profiled_time_limit_ms(safety_factor=1.5, total_wall_time_ms=1000, repetitions=3) == 500
    assert math.ceil(1.5 * math.ceil(1000 / 3)) == 501


def test_one_repetition_is_the_plain_safety_factor() -> None:
    assert profiled_time_limit_ms(safety_factor=2.0, total_wall_time_ms=750, repetitions=1) == 1500


@pytest.mark.parametrize("repetitions", [0, -1])
def test_a_nonsensical_repetition_count_never_divides(repetitions: int) -> None:
    """A zero would be a division by zero and a negative would invert the limit."""
    assert profiled_time_limit_ms(safety_factor=1.0, total_wall_time_ms=800, repetitions=repetitions) == 800


def test_the_limit_never_falls_below_one_millisecond() -> None:
    """A limit of 0 ms would fail the column's own CHECK constraint."""
    assert profiled_time_limit_ms(safety_factor=1.5, total_wall_time_ms=0, repetitions=10) == 1


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        (3000, 3, 1000),  # exact
        (1000, 3, 334),  # rounds up, never down
        (1500, 1, 1500),  # a single run is the identity
        (1, 10, 1),  # never rounds a live limit away to zero
    ],
)
def test_ceil_div_rounds_up(numerator: int, denominator: int, expected: int) -> None:
    assert ceil_div(numerator, denominator) == expected
    # Rounding up is the safety property: the reconstructed budget is never
    # smaller than the value that was converted.
    assert ceil_div(numerator, denominator) * max(1, denominator) >= numerator


@pytest.mark.parametrize("denominator", [0, -3])
def test_ceil_div_treats_a_nonsensical_denominator_as_one(denominator: int) -> None:
    assert ceil_div(500, denominator) == 500
