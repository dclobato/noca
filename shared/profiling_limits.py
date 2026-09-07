#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The one formula that turns a profiling observation into a stored time limit.

Auto-Limit measures a test case across several repetitions and observes their
*sum*, while a stored ``time_limit_ms`` is the mean time for a single run. Two
independent places need that conversion -- the worker that persists the limit
(``autojudge/profiling_job.py``) and the Web layer that recomputes the same
suggestion for the Limits tab
(``web/services/problem_service/profiling.py``) -- and they disagreed before,
which is exactly why the formula lives here instead of in either of them.

Round once. ``ceil(safety_factor * ceil(total / repetitions))`` rounds twice and
inflates the limit by up to a millisecond per rounding, so the division happens
inside the single ``ceil``.
"""

from __future__ import annotations

import math

__all__ = ["ceil_div", "profiled_time_limit_ms"]


def ceil_div(numerator: int, denominator: int) -> int:
    """Divide two positive integers, rounding up, without touching floats.

    Every place that converts a repetition-summed time limit into a per-run one
    -- the schema migration, a legacy package import, a legacy backup restore --
    has to round the same way, or the same problem comes out with a different
    limit depending on the road it travelled. Rounding *up* is the direction
    that matters: it can only ever be more generous than the value it converts,
    never stricter than the contest that was running.
    """
    return -(-numerator // max(1, denominator))


def profiled_time_limit_ms(*, safety_factor: float, total_wall_time_ms: int, repetitions: int) -> int:
    """Return the per-run time limit for a repetition-summed observation.

    Args:
        safety_factor: Multiplier applied to the observed mean.
        total_wall_time_ms: Observed wall time summed over every repetition.
        repetitions: Repetitions the observation was summed across.

    Returns:
        The per-run time limit in milliseconds, never below 1.
    """
    return max(1, math.ceil(safety_factor * total_wall_time_ms / max(1, repetitions)))
