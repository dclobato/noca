#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The judge dispatches on the problem's stored strategy, not on validator rows.

This is the regression surface that matters most in this change: getting it
wrong means a submission is judged by the wrong machinery, silently. The three
cases below are exactly the ones the previous derivation got wrong or could not
express.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from autojudge.db import open_db
from shared.db_schema import problem_custom_validators
from shared.enumerations import CustomValidatorActiveState, CustomValidatorCandidateState, ProblemValidatorType
from web.models.contest import Contest
from web.models.problem import Problem


async def _problem(
    session: AsyncSession,
    contest: Contest,
    strategy: ProblemValidatorType,
    *,
    ordinal: int,
) -> Problem:
    """Create one problem with an explicit stored strategy."""
    problem = Problem(
        contest_id=contest.id,
        title=f"Problem {ordinal}",
        ordinal=ordinal,
        color="#123456",
        validator_type=strategy,
    )
    session.add(problem)
    await session.flush()
    return problem


@pytest.mark.asyncio
async def test_a_standard_problem_with_a_stale_validator_row_dispatches_as_standard(
    engine, session: AsyncSession, running_contest: Contest
) -> None:
    """A leftover validator row must not turn a standard problem interactive.

    The previous derivation read exactly this row and would have judged the
    submission with the interactive machinery.
    """
    problem = await _problem(session, running_contest, ProblemValidatorType.STANDARD, ordinal=1)
    await session.execute(
        problem_custom_validators.insert().values(
            problem_id=problem.id,
            active_language_id="python3",
            active_source="print('stale')",
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=datetime.now(UTC),
        )
    )
    await session.commit()

    async with open_db(engine) as db:
        state = await db.get_custom_validator_dispatch_state("contest", problem.id)

    assert state.strategy is ProblemValidatorType.STANDARD
    assert state.is_interactive is False
    assert state.active is None
    assert state.unsupported_reason is None


@pytest.mark.asyncio
async def test_an_interactive_problem_without_source_is_interactive_and_unavailable(
    engine, session: AsyncSession, running_contest: Contest
) -> None:
    """Removing the source must not silently hand the problem to the comparator.

    The dispatch state still says interactive, with no active revision, which is
    what makes the caller fail the judgment closed.
    """
    problem = await _problem(session, running_contest, ProblemValidatorType.INTERACTIVE, ordinal=1)
    await session.commit()

    async with open_db(engine) as db:
        state = await db.get_custom_validator_dispatch_state("contest", problem.id)

    assert state.is_interactive is True
    assert state.active is None


@pytest.mark.asyncio
async def test_a_pending_candidate_is_not_an_active_revision(
    engine, session: AsyncSession, running_contest: Contest
) -> None:
    """A candidate still compiling is not something to judge with."""
    problem = await _problem(session, running_contest, ProblemValidatorType.INTERACTIVE, ordinal=1)
    await session.execute(
        problem_custom_validators.insert().values(
            problem_id=problem.id,
            candidate_language_id="python3",
            candidate_source="print('candidate')",
            candidate_token=str(uuid.uuid4()),
            candidate_state=CustomValidatorCandidateState.PENDING,
        )
    )
    await session.commit()

    async with open_db(engine) as db:
        state = await db.get_custom_validator_dispatch_state("contest", problem.id)

    assert state.is_interactive is True
    assert state.active is None


@pytest.mark.asyncio
async def test_an_interactive_problem_with_a_valid_revision_dispatches_it(
    engine, session: AsyncSession, running_contest: Contest
) -> None:
    """The complete interactive case still loads its active source."""
    problem = await _problem(session, running_contest, ProblemValidatorType.INTERACTIVE, ordinal=1)
    await session.execute(
        problem_custom_validators.insert().values(
            problem_id=problem.id,
            active_language_id="python3",
            active_source="print('validator')",
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=datetime.now(UTC),
        )
    )
    await session.commit()

    async with open_db(engine) as db:
        state = await db.get_custom_validator_dispatch_state("contest", problem.id)

    assert state.is_interactive is True
    assert state.active is not None
    assert state.active.source_code == "print('validator')"


@pytest.mark.asyncio
async def test_a_stored_checker_strategy_is_unsupported_and_never_standard(
    engine, session: AsyncSession, running_contest: Contest
) -> None:
    """The reserved value fails as unsupported rather than falling back."""
    problem = await _problem(session, running_contest, ProblemValidatorType.OUTPUT_CHECKER, ordinal=1)
    await session.commit()

    async with open_db(engine) as db:
        state = await db.get_custom_validator_dispatch_state("contest", problem.id)

    assert state.strategy is ProblemValidatorType.OUTPUT_CHECKER
    assert state.is_interactive is False
    assert state.unsupported_reason is not None
    assert "not available in this build" in state.unsupported_reason
