#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared refusals for problem judgment-data actions.

Routes and the staged test-case planner must reject unsupported strategies and
stale operations consistently, so their error type and strategy gate live here.
"""

from __future__ import annotations

from shared.enumerations import ProblemValidatorType


class PendingOpsError(ValueError):
    """One submitted set of editor operations cannot be applied as stated."""


def require_supported_strategy(validator_type: ProblemValidatorType) -> None:
    """Refuse a strategy this build cannot judge.

    Args:
        validator_type: The problem's stored strategy.

    Raises:
        PendingOpsError: For ``OUTPUT_CHECKER``, which is a reserved value with no
            runtime behind it. Refusing here keeps it from silently inheriting
            standard semantics, which is what a boolean ``interactive`` flag did.
    """
    if validator_type is ProblemValidatorType.OUTPUT_CHECKER:
        raise PendingOpsError("Output checker validation is not available in this build.")


def unsupported_strategy_message(validator_type: ProblemValidatorType) -> str | None:
    """Return why a strategy cannot be edited in this build, or ``None``.

    Exposed for the retained satellite endpoints, which reduce the strategy to an
    ``interactive`` boolean and would otherwise treat a stored ``checker`` as an
    ordinary standard problem -- editing its cases under semantics it does not
    have.

    Args:
        validator_type: The problem's stored strategy.

    Returns:
        str | None: A message to show the operator, or ``None`` when the strategy
        is supported.
    """
    try:
        require_supported_strategy(validator_type)
    except PendingOpsError as exc:
        return str(exc)
    return None
