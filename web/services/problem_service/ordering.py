#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Ordered problem and test-case mutation helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes as orm_attrs

from web.models.contest import Contest
from web.models.problem import Problem, ProblemTestCase

from .models import OrderedItem


def clamp_ordinal(requested_ordinal: int, *, size: int) -> int:
    """Clamp a requested 1-based ordinal into the valid range for a collection."""
    if size <= 1:
        return 1
    return max(1, min(requested_ordinal, size))


def apply_dense_ordinals[TOrderedItem: OrderedItem](items: list[TOrderedItem]) -> None:
    """Rewrite ordinals to a dense 1..n sequence, avoiding redundant writes."""
    for expected_ordinal, item in enumerate(items, start=1):
        if item.ordinal != expected_ordinal:
            item.ordinal = expected_ordinal


async def bulk_update_ordinals[TOrderedItem: OrderedItem](
    session: AsyncSession,
    table_name: str,
    items: list[TOrderedItem],
) -> None:
    """Atomically update ordinals without transient unique-key collisions."""
    if not items:
        return

    n = len(items)
    in_clause = ", ".join(f":id_{i}" for i in range(n))
    temp_whens = " ".join(f"WHEN :id_{i} THEN CAST(:tmp_ord_{i} AS integer)" for i in range(n))
    final_whens = " ".join(f"WHEN :id_{i} THEN CAST(:ord_{i} AS integer)" for i in range(n))
    params: dict[str, object] = {"now": datetime.now(UTC)}
    temp_base = max((int(item.ordinal) for item in items), default=0) + n
    for i, item in enumerate(items):
        params[f"id_{i}"] = item.id
        params[f"tmp_ord_{i}"] = temp_base + i + 1
        params[f"ord_{i}"] = item.ordinal

    # Suppress autoflush: ``apply_dense_ordinals`` has already mutated the ORM
    # ``ordinal`` attributes, so an autoflush triggered by these executes would
    # emit per-row UPDATEs to the final ordinals in arbitrary order and can
    # transiently violate the (group, ordinal) unique constraint. The raw
    # two-phase update below moves every row through a disjoint temp range first,
    # which is collision-free regardless of order.
    with session.sync_session.no_autoflush:
        await session.execute(
            text(
                f"UPDATE {table_name} SET ordinal = CASE id {temp_whens} END, "
                f"updated_at = :now WHERE id IN ({in_clause})"
            ),
            params,
        )
        await session.execute(
            text(
                f"UPDATE {table_name} SET ordinal = CASE id {final_whens} END, "
                f"updated_at = :now WHERE id IN ({in_clause})"
            ),
            params,
        )
    for item in items:
        orm_attrs.set_committed_value(item, "ordinal", item.ordinal)


async def load_contest_problems(session: AsyncSession, contest_id: str) -> list[Problem]:
    """Load contest problems in stable ordinal order."""
    result = await session.execute(
        select(Problem).where(Problem.contest_id == contest_id).order_by(Problem.ordinal, Problem.id)
    )
    return list(result.scalars().all())


async def load_problem_test_cases(session: AsyncSession, problem_id: str) -> list[ProblemTestCase]:
    """Load problem test cases in stable ordinal order."""
    result = await session.execute(
        select(ProblemTestCase)
        .where(ProblemTestCase.problem_id == problem_id)
        .order_by(ProblemTestCase.ordinal, ProblemTestCase.id)
    )
    return list(result.scalars().all())


async def append_problem(
    session: AsyncSession,
    contest: Contest,
    problem: Problem,
) -> Problem:
    """Attach a new problem to the end of a contest's ordered problem list."""
    if problem.contest_id and problem.contest_id != contest.id:
        raise ValueError("Problem already belongs to a different contest.")

    problems = await load_contest_problems(session, contest.id)
    problem.contest = contest
    problem.ordinal = len(problems) + 1
    session.add(problem)
    await session.flush()
    return problem


async def append_test_case(
    session: AsyncSession,
    problem: Problem,
    test_case: ProblemTestCase,
) -> ProblemTestCase:
    """Attach a new test case to the end of a problem's ordered test-case list."""
    if test_case.problem_id and test_case.problem_id != problem.id:
        raise ValueError("Test case already belongs to a different problem.")

    test_cases = await load_problem_test_cases(session, problem.id)
    test_case.problem = problem
    test_case.ordinal = len(test_cases) + 1
    session.add(test_case)
    await session.flush()
    return test_case


async def move_problem(
    session: AsyncSession,
    contest: Contest,
    problem: Problem,
    new_ordinal: int,
) -> None:
    """Move a problem to a new 1-based position inside its contest."""
    if problem.contest_id != contest.id:
        raise ValueError("Problem does not belong to the provided contest.")

    problems = await load_contest_problems(session, contest.id)
    current_index = next((index for index, item in enumerate(problems) if item.id == problem.id), None)
    if current_index is None:
        raise ValueError("Problem was not found in the provided contest.")

    moving_problem = problems.pop(current_index)
    destination_index = clamp_ordinal(new_ordinal, size=len(problems) + 1) - 1
    problems.insert(destination_index, moving_problem)

    apply_dense_ordinals(problems)
    await bulk_update_ordinals(session, "problems", problems)


async def move_test_case(
    session: AsyncSession,
    problem: Problem,
    test_case: ProblemTestCase,
    new_ordinal: int,
) -> None:
    """Move a test case to a new 1-based position inside its problem."""
    if test_case.problem_id != problem.id:
        raise ValueError("Test case does not belong to the provided problem.")

    test_cases = await load_problem_test_cases(session, problem.id)
    current_index = next((index for index, item in enumerate(test_cases) if item.id == test_case.id), None)
    if current_index is None:
        raise ValueError("Test case was not found in the provided problem.")

    moving_test_case = test_cases.pop(current_index)
    destination_index = clamp_ordinal(new_ordinal, size=len(test_cases) + 1) - 1
    test_cases.insert(destination_index, moving_test_case)

    apply_dense_ordinals(test_cases)
    await bulk_update_ordinals(session, "test_cases", test_cases)


async def remove_problem_and_resequence(
    session: AsyncSession,
    contest: Contest,
    problem: Problem,
) -> None:
    """Remove a problem from a contest and close ordinal gaps."""
    if problem.contest_id != contest.id:
        raise ValueError("Problem does not belong to the provided contest.")

    problems = await load_contest_problems(session, contest.id)
    remaining_problems = [item for item in problems if item.id != problem.id]

    # Delete the removed row first so it can never collide with the temp ordinals
    # used while resequencing the survivors, then close the gap densely.
    await session.delete(problem)
    await session.flush()
    apply_dense_ordinals(remaining_problems)
    await bulk_update_ordinals(session, "problems", remaining_problems)


async def remove_test_case_and_resequence(
    session: AsyncSession,
    problem: Problem,
    test_case: ProblemTestCase,
) -> None:
    """Remove a test case from a problem and close ordinal gaps."""
    if test_case.problem_id != problem.id:
        raise ValueError("Test case does not belong to the provided problem.")

    test_cases = await load_problem_test_cases(session, problem.id)
    remaining_test_cases = [item for item in test_cases if item.id != test_case.id]

    # Delete the removed row first so it can never collide with the temp ordinals
    # used while resequencing the survivors, then close the gap densely.
    await session.delete(test_case)
    await session.flush()
    apply_dense_ordinals(remaining_test_cases)
    await bulk_update_ordinals(session, "test_cases", remaining_test_cases)
