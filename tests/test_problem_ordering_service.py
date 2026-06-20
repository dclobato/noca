#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Regression tests for ordered problem and test-case mutations."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from web.models.contest import Contest
from web.models.problem import Problem, ProblemTestCase
from web.services.problem_service import (
    move_problem,
    move_test_case,
    remove_problem_and_resequence,
    remove_test_case_and_resequence,
    reorder_testcase_files,
    save_testcase_files,
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
        ),
        Problem(
            contest_id=stopped_contest.id,
            title="Problem B",
            ordinal=2,
            color="#00ff00",
        ),
        Problem(
            contest_id=stopped_contest.id,
            title="Problem C",
            ordinal=3,
            color="#0000ff",
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
        Problem(contest_id=stopped_contest.id, title="Problem A", ordinal=1, color="#ff0000"),
        Problem(contest_id=stopped_contest.id, title="Problem B", ordinal=2, color="#00ff00"),
        Problem(contest_id=stopped_contest.id, title="Problem C", ordinal=3, color="#0000ff"),
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
async def test_move_test_case_to_arbitrary_ordinal_resequences_dense(
    session: AsyncSession,
    contest_problem: Problem,
) -> None:
    """Moving test cases by arbitrary ordinal keeps dense database ordinals."""
    test_cases = [
        ProblemTestCase(problem_id=contest_problem.id, ordinal=1),
        ProblemTestCase(problem_id=contest_problem.id, ordinal=2),
        ProblemTestCase(problem_id=contest_problem.id, ordinal=3),
    ]
    session.add_all(test_cases)
    await session.flush()

    await move_test_case(session, contest_problem, test_cases[0], 3)
    await session.commit()

    result = await session.execute(
        select(ProblemTestCase.id, ProblemTestCase.ordinal)
        .where(ProblemTestCase.problem_id == contest_problem.id)
        .order_by(ProblemTestCase.ordinal)
    )

    assert result.all() == [(test_cases[1].id, 1), (test_cases[2].id, 2), (test_cases[0].id, 3)]


def test_reorder_testcase_files_preserves_moved_content(tmp_path) -> None:
    """Non-adjacent file reorders should keep testcase contents with their row."""
    problem_id = "problem-1"
    save_testcase_files(problem_id, 1, b"in-1", b"out-1", tmp_path)
    save_testcase_files(problem_id, 2, b"in-2", b"out-2", tmp_path)
    save_testcase_files(problem_id, 3, b"in-3", b"out-3", tmp_path)

    reorder_testcase_files(problem_id, {1: 3, 2: 1, 3: 2}, tmp_path)

    base = tmp_path / problem_id
    assert (base / "001.in").read_bytes() == b"in-2"
    assert (base / "001.out").read_bytes() == b"out-2"
    assert (base / "002.in").read_bytes() == b"in-3"
    assert (base / "002.out").read_bytes() == b"out-3"
    assert (base / "003.in").read_bytes() == b"in-1"
    assert (base / "003.out").read_bytes() == b"out-1"
