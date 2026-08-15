#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""One decision for "can this problem be judged in its current state".

Incomplete drafts are explicitly allowed: a problem may be saved with no test
cases and no validator source, which is what makes "choose a strategy, create,
then upload the validator" possible. Judgeability is therefore enforced at the
**execution gates** -- submission creation, solution tests, Arena enablement,
AutoJudge dispatch, and full export -- and never at save time.

Both identity domains and the worker ask the same question, so the rule lives
here rather than being restated per domain, where the two could drift. The input
is a small bundle of domain-neutral *facts*: this module runs no query and
imports nothing from ``web`` or ``arena``, so each caller gathers the facts with
whatever query shape suits it.

The decision reads the problem's stored strategy. It never infers the strategy
from validator source presence -- that inference is what this release removed.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.enumerations import ProblemValidatorType


@dataclass(frozen=True)
class ProblemJudgeabilityFacts:
    """What a gate must know about a problem to decide whether it can be judged.

    Attributes:
        strategy: The problem's stored validation strategy.
        total_case_count: Number of test cases the problem has.
        secret_case_count: Number of those cases that are not samples.
        cases_missing_expected_output: How many cases have no expected-output
            file. A present-but-empty file is valid output and is not counted.
        has_active_valid_validator: Whether an active ``VALID`` validator
            revision exists. Only meaningful for an interactive problem.
    """

    strategy: ProblemValidatorType
    total_case_count: int
    secret_case_count: int
    cases_missing_expected_output: int
    has_active_valid_validator: bool


def judgeability_error(facts: ProblemJudgeabilityFacts) -> str | None:
    """Return why the problem cannot be judged yet, or ``None`` when it can.

    A **standard** problem is judgeable with at least one test case, every one of
    which has a present expected-output file.

    An **interactive** problem is judgeable with at least one secret input-only
    case and an active ``VALID`` validator revision. Losing its validator source
    leaves it interactive and non-judgeable -- never silently comparable by
    tokens against cases that have no expected output.

    Any other strategy -- currently the reserved output checker -- is **never**
    judgeable. It is rejected explicitly rather than by falling through, so a new
    strategy can never inherit the standard rules by accident.

    Args:
        facts: The gathered facts about the problem.

    Returns:
        str | None: An operator-facing reason, or ``None`` when judgeable.
    """
    if facts.strategy is ProblemValidatorType.STANDARD:
        if facts.total_case_count < 1:
            return "This problem has no test cases yet."
        if facts.cases_missing_expected_output > 0:
            return "This problem has test cases with no expected output."
        return None

    if facts.strategy is ProblemValidatorType.INTERACTIVE:
        if not facts.has_active_valid_validator:
            return "This interactive problem has no active valid custom validator."
        if facts.secret_case_count < 1:
            return "This interactive problem has no secret test cases yet."
        return None

    return "Output checker validation is not available in this build."
