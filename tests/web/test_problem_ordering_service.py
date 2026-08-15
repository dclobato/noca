#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Regression tests for ordered problem and test-case mutations."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import ProblemValidatorType
from web.models.contest import Contest
from web.models.problem import Problem, ProblemTestCase
from web.services.problem_service import (
    move_problem,
    remove_problem_and_resequence,
    remove_test_case_and_resequence,
)


@pytest.mark.asyncio
async def test_remove_first_problem_resequences_without_unique_collision(
    session: AsyncSession,
    stopped_contest: Contest,
) -> None:
    """Removing ordinal 1 frees the slot before survivors are resequenced."""
    problems = [
        Problem(
            contest_id=stopped_contest.id,
            title="Problem A",
            ordinal=1,
            color="#ff0000",
            validator_type=ProblemValidatorType.STANDARD,
        ),
        Problem(
            contest_id=stopped_contest.id,
            title="Problem B",
            ordinal=2,
            color="#00ff00",
            validator_type=ProblemValidatorType.STANDARD,
        ),
        Problem(
            contest_id=stopped_contest.id,
            title="Problem C",
            ordinal=3,
            color="#0000ff",
            validator_type=ProblemValidatorType.STANDARD,
        ),
    ]
    session.add_all(problems)
    await session.flush()

    await remove_problem_and_resequence(session, stopped_contest, problems[0])
    await session.commit()

    problem_titles = select(Problem.title, Problem.ordinal)
    contest_problem_titles = problem_titles.where(Problem.contest_id == stopped_contest.id)
    query = contest_problem_titles.order_by(Problem.ordinal)
    result = await session.execute(query)

    assert result.all() == [("Problem B", 1), ("Problem C", 2)]


@pytest.mark.asyncio
async def test_move_problem_to_arbitrary_ordinal_resequences_dense(
    session: AsyncSession,
    stopped_contest: Contest,
) -> None:
    """Moving problems first-to-last and last-to-first keeps dense ordinals."""
    problems = [
        Problem(
            contest_id=stopped_contest.id,
            title="Problem A",
            ordinal=1,
            color="#ff0000",
            validator_type=ProblemValidatorType.STANDARD,
        ),
        Problem(
            contest_id=stopped_contest.id,
            title="Problem B",
            ordinal=2,
            color="#00ff00",
            validator_type=ProblemValidatorType.STANDARD,
        ),
        Problem(
            contest_id=stopped_contest.id,
            title="Problem C",
            ordinal=3,
            color="#0000ff",
            validator_type=ProblemValidatorType.STANDARD,
        ),
    ]
    session.add_all(problems)
    await session.flush()

    await move_problem(session, stopped_contest, problems[0], 3)
    await move_problem(session, stopped_contest, problems[0], 1)
    await session.commit()

    result = await session.execute(
        select(Problem.title, Problem.ordinal).where(Problem.contest_id == stopped_contest.id).order_by(Problem.ordinal)
    )

    assert result.all() == [("Problem A", 1), ("Problem B", 2), ("Problem C", 3)]


@pytest.mark.asyncio
async def test_remove_first_test_case_resequences_without_unique_collision(
    session: AsyncSession,
    contest_problem: Problem,
) -> None:
    """Removing test-case ordinal 1 frees the slot before survivors are resequenced."""
    test_cases = [
        ProblemTestCase(problem_id=contest_problem.id, ordinal=1),
        ProblemTestCase(problem_id=contest_problem.id, ordinal=2),
        ProblemTestCase(problem_id=contest_problem.id, ordinal=3),
    ]
    session.add_all(test_cases)
    await session.flush()

    await remove_test_case_and_resequence(session, contest_problem, test_cases[0])
    await session.commit()

    result = await session.execute(
        select(ProblemTestCase.id, ProblemTestCase.ordinal)
        .where(ProblemTestCase.problem_id == contest_problem.id)
        .order_by(ProblemTestCase.ordinal)
    )

    assert result.all() == [(test_cases[1].id, 1), (test_cases[2].id, 2)]


@pytest.mark.asyncio
async def test_remove_test_case_resequences_many_without_collision(
    session: AsyncSession,
    contest_problem: Problem,
) -> None:
    """Removing a middle case from a large set resequences without a unique collision.

    Regression for the resequence path: with >=4 test cases the previous
    park-then-resequence approach moved the deleted row into the same temp
    ordinal range used for the survivors, violating the
    (problem_id, ordinal) unique constraint.
    """
    test_cases = [ProblemTestCase(problem_id=contest_problem.id, ordinal=n) for n in range(1, 13)]
    session.add_all(test_cases)
    await session.flush()

    # Remove a middle case (ordinal 5).
    await remove_test_case_and_resequence(session, contest_problem, test_cases[4])
    await session.commit()

    rows = (
        (
            await session.execute(
                select(ProblemTestCase.ordinal)
                .where(ProblemTestCase.problem_id == contest_problem.id)
                .order_by(ProblemTestCase.ordinal)
            )
        )
        .scalars()
        .all()
    )

    # 11 survivors, densely numbered 1..11.
    assert list(rows) == list(range(1, 12))
