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


def test_contestant_error_precedes_clean_validator_exit() -> None:
    result = classify_interactive_outcome(InteractiveOutcome(7, None, 0, None))
    assert result.verdict == Verdict.RE
