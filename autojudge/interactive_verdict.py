#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Pure verdict classification for custom interactive validator attempts."""

from __future__ import annotations

from dataclasses import dataclass

from shared.enumerations import CustomValidatorCrashReason, Verdict


@dataclass(frozen=True)
class InteractiveOutcome:
    """Inputs needed to classify a completed interactive attempt."""

    contestant_exit_code: int | None
    contestant_signal: int | None
    validator_exit_code: int | None
    validator_signal: int | None
    memory_limit_reached: bool = False
    output_limit_reached: bool = False
    crash_reason: CustomValidatorCrashReason | None = None


@dataclass(frozen=True)
class InteractiveVerdict:
    """Classified outcome; ``verdict=None`` denotes an internal failure."""

    verdict: Verdict | None
    retryable_validator_failure: bool


def classify_interactive_outcome(outcome: InteractiveOutcome) -> InteractiveVerdict:
    """Apply the documented interactive verdict precedence exactly."""
    if outcome.memory_limit_reached:
        return InteractiveVerdict(Verdict.MLE, False)
    if outcome.output_limit_reached:
        return InteractiveVerdict(Verdict.OLE, False)
    if outcome.crash_reason is not None or outcome.validator_signal is not None or outcome.validator_exit_code is None:
        return InteractiveVerdict(None, True)
    if outcome.contestant_signal is not None or outcome.contestant_exit_code not in (None, 0):
        return InteractiveVerdict(Verdict.RE, False)
    validator_verdicts = {0: Verdict.AC, 1: Verdict.WA, 2: Verdict.TLE, 4: Verdict.PE}
    return InteractiveVerdict(validator_verdicts.get(outcome.validator_exit_code, Verdict.RE), False)
