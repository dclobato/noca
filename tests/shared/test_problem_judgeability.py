#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The shared judgeability contract every execution gate applies.

These are the rules a draft must satisfy before it can be submitted to, tested
against, enabled, dispatched, or fully exported. Saving an incomplete draft is
always allowed; only these gates refuse one.
"""

from __future__ import annotations

import pytest

from shared.enumerations import ProblemValidatorType
from shared.services.problem_judgeability import ProblemJudgeabilityFacts, judgeability_error


def _facts(
    strategy: ProblemValidatorType,
    *,
    total: int = 0,
    secret: int = 0,
    missing_output: int = 0,
    valid_validator: bool = False,
) -> ProblemJudgeabilityFacts:
    """Build a facts bundle with everything but the named fields empty."""
    return ProblemJudgeabilityFacts(
        strategy=strategy,
        total_case_count=total,
        secret_case_count=secret,
        cases_missing_expected_output=missing_output,
        has_active_valid_validator=valid_validator,
    )


def test_a_standard_problem_with_complete_cases_is_judgeable() -> None:
    """One case, expected output present on all of them."""
    assert judgeability_error(_facts(ProblemValidatorType.STANDARD, total=1, secret=1)) is None


def test_a_standard_problem_with_no_cases_is_refused() -> None:
    """An empty draft saves, but cannot be judged."""
    error = judgeability_error(_facts(ProblemValidatorType.STANDARD))

    assert error is not None
    assert "no test cases" in error


def test_a_standard_problem_missing_an_expected_output_is_refused() -> None:
    """A token compare has nothing to compare against without expected output."""
    error = judgeability_error(_facts(ProblemValidatorType.STANDARD, total=2, missing_output=1))

    assert error is not None
    assert "no expected output" in error


def test_an_empty_expected_output_file_is_valid_output() -> None:
    """Present-but-empty is a legitimate expected output, not a missing one.

    The facts count *missing files*, so a problem whose outputs are all empty
    reports zero missing and stays judgeable.
    """
    assert judgeability_error(_facts(ProblemValidatorType.STANDARD, total=1, missing_output=0)) is None


def test_an_interactive_problem_with_a_valid_validator_and_secret_cases_is_judgeable() -> None:
    """The complete interactive shape."""
    facts = _facts(ProblemValidatorType.INTERACTIVE, total=1, secret=1, valid_validator=True)

    assert judgeability_error(facts) is None


def test_an_interactive_problem_without_an_active_revision_is_refused() -> None:
    """Removing the source leaves it interactive and simply unjudgeable.

    This is the case the previous inference got wrong: it silently became a
    standard problem and was token-compared against cases carrying no output.
    """
    facts = _facts(ProblemValidatorType.INTERACTIVE, total=1, secret=1, valid_validator=False)
    error = judgeability_error(facts)

    assert error is not None
    assert "no active valid custom validator" in error


def test_an_interactive_problem_without_secret_cases_is_refused() -> None:
    """A validator is replayed once per case, so it needs at least one."""
    facts = _facts(ProblemValidatorType.INTERACTIVE, total=0, secret=0, valid_validator=True)
    error = judgeability_error(facts)

    assert error is not None
    assert "no secret test cases" in error


def test_missing_expected_output_never_blocks_an_interactive_problem() -> None:
    """Interactive cases carry input only; that is the point, not a defect."""
    facts = _facts(
        ProblemValidatorType.INTERACTIVE,
        total=3,
        secret=3,
        missing_output=3,
        valid_validator=True,
    )

    assert judgeability_error(facts) is None


@pytest.mark.parametrize(
    "facts",
    [
        _facts(ProblemValidatorType.OUTPUT_CHECKER),
        _facts(ProblemValidatorType.OUTPUT_CHECKER, total=5, secret=5, valid_validator=True),
    ],
    ids=["empty", "otherwise complete"],
)
def test_the_reserved_output_checker_is_never_judgeable(facts: ProblemJudgeabilityFacts) -> None:
    """It is refused explicitly, never by falling through to the standard rules.

    An otherwise complete checker problem is the case that matters: if the
    decision fell through, it would be judged by the token comparator.
    """
    error = judgeability_error(facts)

    assert error is not None
    assert "not available in this build" in error
