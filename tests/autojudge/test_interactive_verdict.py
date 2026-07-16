#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import pytest

from autojudge.interactive_verdict import InteractiveOutcome, classify_interactive_outcome
from shared.enumerations import CustomValidatorCrashReason, Verdict


@pytest.mark.parametrize(
    ("exit_code", "verdict"),
    [(0, Verdict.AC), (1, Verdict.WA), (2, Verdict.TLE), (4, Verdict.PE)],
)
def test_documented_validator_exits(exit_code: int, verdict: Verdict) -> None:
    result = classify_interactive_outcome(InteractiveOutcome(0, None, exit_code, None))
    assert result.verdict == verdict
    assert result.retryable_validator_failure is False


@pytest.mark.parametrize("exit_code", [3, 5, 42, 255])
def test_undocumented_clean_validator_exit_is_contestant_re(exit_code: int) -> None:
    result = classify_interactive_outcome(InteractiveOutcome(0, None, exit_code, None))
    assert result == (result.__class__)(Verdict.RE, False)


@pytest.mark.parametrize("reason", list(CustomValidatorCrashReason))
def test_validator_failure_is_internal_and_retryable(reason: CustomValidatorCrashReason) -> None:
    result = classify_interactive_outcome(InteractiveOutcome(0, None, None, None, crash_reason=reason))
    assert result.verdict is None
    assert result.retryable_validator_failure is True


def test_resource_limits_precede_missing_validator_exit() -> None:
    memory = classify_interactive_outcome(InteractiveOutcome(None, 9, None, 9, memory_limit_reached=True))
    output = classify_interactive_outcome(InteractiveOutcome(None, 9, None, 9, output_limit_reached=True))
    assert memory.verdict == Verdict.MLE
    assert output.verdict == Verdict.OLE


def test_validator_exit_precedes_later_contestant_error() -> None:
    result = classify_interactive_outcome(InteractiveOutcome(7, None, 2, None, finished_first="validator"))
    assert result.verdict == Verdict.TLE


def test_clean_validator_exit_wins_after_later_watchdog() -> None:
    result = classify_interactive_outcome(
        InteractiveOutcome(
            None,
            None,
            1,
            None,
            crash_reason=CustomValidatorCrashReason.WATCHDOG,
            finished_first="validator",
        )
    )
    assert result.verdict == Verdict.WA
    assert result.retryable_validator_failure is False


def test_clean_contestant_exit_first_still_honours_the_validator_verdict() -> None:
    # A contestant that prints its final answer and exits 0 races the validator's own
    # exit; the observed order must not decide the verdict.
    accepted = classify_interactive_outcome(InteractiveOutcome(0, None, 0, None, finished_first="contestant"))
    assert accepted.verdict == Verdict.AC
    assert accepted.retryable_validator_failure is False

    timed_out = classify_interactive_outcome(InteractiveOutcome(0, None, 2, None, finished_first="contestant"))
    assert timed_out.verdict == Verdict.TLE


@pytest.mark.parametrize(
    ("exit_code", "signal"),
    [(1, None), (139, None), (None, 11)],
)
def test_contestant_crashing_first_is_runtime_error(exit_code: int | None, signal: int | None) -> None:
    result = classify_interactive_outcome(InteractiveOutcome(exit_code, signal, 1, None, finished_first="contestant"))
    assert result.verdict == Verdict.RE
    assert result.retryable_validator_failure is False


def test_contestant_exit_first_without_validator_verdict_is_runtime_error() -> None:
    result = classify_interactive_outcome(InteractiveOutcome(0, None, None, None, finished_first="contestant"))
    assert result.verdict == Verdict.RE


def test_validator_signal_precedes_contestant_first_exit() -> None:
    result = classify_interactive_outcome(InteractiveOutcome(0, None, None, 11, finished_first="contestant"))
    assert result.verdict is None
    assert result.retryable_validator_failure is True
