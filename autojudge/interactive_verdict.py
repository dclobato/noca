#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Pure verdict classification for custom interactive validator attempts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from shared.enumerations import CustomValidatorCrashReason, Verdict

FinishedFirst = Literal["contestant", "validator"]


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
    finished_first: FinishedFirst | None = None


@dataclass(frozen=True)
class InteractiveVerdict:
    """Classified outcome; ``verdict=None`` denotes an internal failure."""

    verdict: Verdict | None
    retryable_validator_failure: bool


def _contestant_crashed(outcome: InteractiveOutcome) -> bool:
    """Report whether the contestant died rather than terminated normally."""
    if outcome.contestant_signal is not None:
        return True
    return outcome.contestant_exit_code not in (0, None)


def classify_interactive_outcome(outcome: InteractiveOutcome) -> InteractiveVerdict:
    """Apply the documented interactive verdict precedence exactly."""
    if outcome.memory_limit_reached:
        return InteractiveVerdict(Verdict.MLE, False)
    if outcome.output_limit_reached:
        return InteractiveVerdict(Verdict.OLE, False)
    if outcome.validator_signal is not None:
        return InteractiveVerdict(None, True)
    if outcome.crash_reason in {
        CustomValidatorCrashReason.STARTUP,
        CustomValidatorCrashReason.COMMUNICATION,
    }:
        return InteractiveVerdict(None, True)
    clean_validator_exit: int | None = None
    if outcome.validator_exit_code is not None and (
        outcome.finished_first == "validator" or outcome.crash_reason is None
    ):
        clean_validator_exit = outcome.validator_exit_code
    # Ending first is only a broken protocol when the contestant died mid-conversation.
    # A contestant that exits 0 first has merely stopped after its final answer, and the
    # two exits race, so trusting the order there would make a correct solution flaky.
    if outcome.finished_first == "contestant" and (_contestant_crashed(outcome) or clean_validator_exit is None):
        return InteractiveVerdict(Verdict.RE, False)
    if clean_validator_exit is not None:
        validator_verdicts = {0: Verdict.AC, 1: Verdict.WA, 2: Verdict.TLE, 4: Verdict.PE}
        return InteractiveVerdict(validator_verdicts.get(clean_validator_exit, Verdict.RE), False)
    if outcome.crash_reason is not None or outcome.validator_exit_code is None:
        return InteractiveVerdict(None, True)
    return InteractiveVerdict(Verdict.RE, False)
