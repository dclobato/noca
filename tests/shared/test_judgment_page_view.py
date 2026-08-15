#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Judgeability summaries for the shared judgment-data editor."""

from dataclasses import replace

from shared.enumerations import ProblemValidatorType
from shared.services.custom_validator import status_view
from shared.services.judgment_page_view import build_judgment_readiness
from shared.services.problem_judgeability import ProblemJudgeabilityFacts


def _facts(
    strategy: ProblemValidatorType,
    *,
    total: int,
    secret: int = 0,
    missing_output: int = 0,
    active_validator: bool = False,
) -> ProblemJudgeabilityFacts:
    """Return canonical facts for one readiness scenario."""
    return ProblemJudgeabilityFacts(
        strategy=strategy,
        total_case_count=total,
        secret_case_count=secret,
        cases_missing_expected_output=missing_output,
        has_active_valid_validator=active_validator,
    )


def test_a_standard_problem_without_cases_is_incomplete() -> None:
    """A validator-free strategy still needs at least one test case."""
    readiness = build_judgment_readiness(
        facts=_facts(ProblemValidatorType.STANDARD, total=0),
        validator_status=status_view(None),
    )

    assert readiness.state == "incomplete"
    assert readiness.label == "Judgment data incomplete"
    assert readiness.detail == "Add at least one test case."


def test_a_standard_problem_with_cases_is_ready() -> None:
    """The summary carries the module-owned rejudge handoff unchanged."""
    readiness = build_judgment_readiness(
        facts=_facts(ProblemValidatorType.STANDARD, total=2),
        validator_status=status_view(None),
        has_submissions=True,
        rejudge_url="/rejudge",
        rejudge_return_path="/judgment/test-cases",
    )

    assert readiness.state == "ready"
    assert readiness.detail == "2 test cases are configured."
    assert readiness.has_submissions is True
    assert readiness.rejudge_url == "/rejudge"
    assert readiness.rejudge_return_path == "/judgment/test-cases"


def test_an_interactive_problem_waiting_for_its_first_validator_is_pending() -> None:
    """Compilation is distinct from a missing or failed validator."""
    pending = replace(status_view(None), configured=True, usable=False, polling=True)

    readiness = build_judgment_readiness(
        facts=_facts(ProblemValidatorType.INTERACTIVE, total=1, secret=1),
        validator_status=pending,
    )

    assert readiness.state == "pending"
    assert readiness.label == "Validator compiling"


def test_an_active_interactive_problem_stays_ready_during_replacement() -> None:
    """A compiling candidate does not hide a still-valid active revision."""
    active_with_candidate = replace(status_view(None), configured=True, usable=True, polling=True)

    readiness = build_judgment_readiness(
        facts=_facts(
            ProblemValidatorType.INTERACTIVE,
            total=3,
            secret=3,
            active_validator=True,
        ),
        validator_status=active_with_candidate,
    )

    assert readiness.state == "ready"
    assert readiness.detail == "The active validator is ready while a replacement compiles."


def test_an_unusable_interactive_validator_has_a_recovery_step() -> None:
    """A failed or runtime-disabled validator is not presented as ready."""
    unavailable = replace(status_view(None), configured=True, usable=False)

    readiness = build_judgment_readiness(
        facts=_facts(ProblemValidatorType.INTERACTIVE, total=1, secret=1),
        validator_status=unavailable,
    )

    assert readiness.state == "incomplete"
    assert readiness.detail == "Upload and validate an interactive validator."


def test_missing_standard_outputs_never_read_as_ready() -> None:
    """The header follows the same storage contract as execution gates."""
    readiness = build_judgment_readiness(
        facts=_facts(ProblemValidatorType.STANDARD, total=2, missing_output=1),
        validator_status=status_view(None),
    )

    assert readiness.state == "incomplete"
    assert readiness.detail == "Replace every test case that has no expected output."
