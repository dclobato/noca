#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""How an archive's problem rows answer "what kind of problem is this".

There are two archive shapes that call themselves version 1, and conflating them
corrupts data. Archives captured before ``problems.validator_type`` existed carry
no strategy at all. Archives captured *after* that column landed but before this
format was bumped carry the strategy while still being labelled version 1 -- the
window in which ``format_version`` understated the payload.

So the rule is **explicit wins, infer only on absence**, and it is written once
here because two callers must agree on it: the integrity checker (deciding
whether a ``.out`` payload member is required) and the restorer (deciding what to
write to the column). If they disagreed, an archive would validate under one
strategy and restore under another.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shared.enumerations import ProblemValidatorType


def strategy_for_backup_problem(
    problem: Mapping[str, Any],
    custom_validator: Mapping[str, Any] | None,
) -> ProblemValidatorType:
    """Return the validation strategy an archived problem row describes.

    Args:
        problem: The archived ``problems`` row.
        custom_validator: The archived ``problem_custom_validators`` row for that
            problem, or ``None`` when the archive carries none.

    Returns:
        ProblemValidatorType: The stored strategy when the row states one;
        otherwise the legacy inference, which is all such a row carries.
    """
    stored = problem.get("validator_type")
    if stored is not None:
        return ProblemValidatorType(stored)
    # Pre-strategy archive. The inference is wrong in general -- that is why this
    # release replaced it -- but it reproduces exactly the behavior the archive
    # was captured under, and it is the only information the archive holds.
    if custom_validator is not None:
        return ProblemValidatorType.INTERACTIVE
    return ProblemValidatorType.STANDARD
