#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Applying one Contest problem Save's test-case plan to the ORM.

The plan itself is decided in :mod:`shared.services.testcase_save_plan`, which
knows nothing about either module's models; this is the Contest half of joining
that decision to rows. Its Arena counterpart is
``arena.services.admin_problem_tc_pending``.

Nothing here touches the filesystem. By the time these rows are written the
Save's complete desired directory already exists in staging, and the swap will
rename it in as part of the commit -- which is the whole point: no row is ever
committed ahead of a file write that could still fail.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.testcase_save_plan import CurrentCase, MaterializedCase
from web.models.problem import Problem, ProblemTestCase
from web.services.problem_service.ordering import load_problem_test_cases


def current_cases(test_cases: Sequence[ProblemTestCase]) -> list[CurrentCase]:
    """Describe a problem's current test cases for the planner.

    Args:
        test_cases: The problem's rows, in any order.

    Returns:
        list[CurrentCase]: The planner's view of them.
    """
    return [
        CurrentCase(id=test_case.id, ordinal=test_case.ordinal, is_sample=test_case.is_sample)
        for test_case in sorted(test_cases, key=lambda item: item.ordinal)
    ]


async def apply_materialized_cases(
    session: AsyncSession,
    problem: Problem,
    materialized: Sequence[MaterializedCase],
) -> None:
    """Make the problem's rows describe the staged directory exactly.

    Every row is first pushed into a disjoint high range, then the dropped ones
    are deleted, and only then do the survivors and the added rows take their
    final 1..n positions in one flush. That order exists because
    ``(problem_id, ordinal)`` is unique, a flush emits UPDATEs before DELETEs, and
    ``web.models.problem`` maintains dense ordinals on every flush -- so a naive
    delete-then-renumber collides with the row it is deleting.

    Args:
        session: The Save's session. Nothing is committed here.
        problem: The problem being saved.
        materialized: The staged cases, in final order.
    """
    existing = {test_case.id: test_case for test_case in await load_problem_test_cases(session, problem.id)}
    kept: dict[str, MaterializedCase] = {
        case.plan.source_id: case for case in materialized if case.plan.source_id is not None
    }

    # Push every row into a disjoint high range first. ``(problem_id, ordinal)`` is
    # unique and a flush emits its UPDATEs before its DELETEs, so renumbering a
    # survivor into an ordinal a row being deleted still holds would collide --
    # which is exactly what the model's ordinal-maintenance hook would attempt on
    # the deletion flush below.
    offset = len(existing) + len(materialized)
    if existing:
        await session.execute(
            update(ProblemTestCase)
            .where(ProblemTestCase.problem_id == problem.id)
            .values(ordinal=ProblemTestCase.ordinal + offset)
        )
        await session.flush()

    for test_case_id, test_case in existing.items():
        if test_case_id not in kept:
            await session.delete(test_case)
    await session.flush()

    # Survivors take their final positions and the added rows are appended in the
    # same flush, so the maintenance hook sees an already-dense 1..n sequence and
    # leaves it alone.
    for case in materialized:
        source_id = case.plan.source_id
        if source_id is None:
            test_case = ProblemTestCase(
                ordinal=case.plan.ordinal,
                is_sample=case.plan.is_sample,
                explanation=case.plan.explanation,
                input_size_bytes=case.input_size_bytes,
                output_size_bytes=case.output_size_bytes,
            )
            test_case.problem = problem
            session.add(test_case)
            continue
        test_case = existing[source_id]
        test_case.ordinal = case.plan.ordinal
        test_case.is_sample = case.plan.is_sample
        if case.plan.set_explanation:
            test_case.explanation = case.plan.explanation
        test_case.input_size_bytes = case.input_size_bytes
        test_case.output_size_bytes = case.output_size_bytes
    await session.flush()
