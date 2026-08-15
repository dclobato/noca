#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Applying one Arena problem Save's test-case plan to the ORM.

The plan is decided in :mod:`shared.services.testcase_save_plan`, which knows
nothing about either module's models; this is the Arena half of joining that
decision to rows, and ``web.services.problem_edit_save`` is the Contest half.

Filesystem work is no longer deferred to callbacks that run after the commit.
Every case the Save wants already exists in a staging directory by the time these
rows are written, and the artifact swap renames that directory in as part of the
commit -- so a rolled-back Save cannot leave rows describing files that were never
written, nor files describing rows that were never committed.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaProblem, ArenaTestCase
from shared.services.testcase_save_plan import CurrentCase, MaterializedCase


def _now() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(UTC)


async def load_current_cases(session: AsyncSession, problem_id: str) -> list[CurrentCase]:
    """Describe a problem's current test cases for the planner.

    Args:
        session: Open Arena session.
        problem_id: The problem being saved.

    Returns:
        list[CurrentCase]: The planner's view of the rows, in ordinal order.
    """
    result = await session.execute(
        select(ArenaTestCase).where(ArenaTestCase.problem_id == problem_id).order_by(ArenaTestCase.ordinal)
    )
    return [CurrentCase(id=row.id, ordinal=row.ordinal, is_sample=row.is_sample) for row in result.scalars().all()]


async def apply_materialized_cases(
    session: AsyncSession,
    problem: ArenaProblem,
    materialized: Sequence[MaterializedCase],
) -> None:
    """Make the problem's rows describe the staged directory exactly.

    Dropped rows are deleted first so their ordinals are free, survivors then move
    through a disjoint temporary range -- ``(problem_id, ordinal)`` is unique, and
    a direct renumbering would collide midway -- and added rows take the positions
    past the end.

    Args:
        session: The Save's session. Nothing is committed here.
        problem: The problem being saved.
        materialized: The staged cases, in final order.
    """
    result = await session.execute(select(ArenaTestCase).where(ArenaTestCase.problem_id == problem.id))
    existing = {row.id: row for row in result.scalars().all()}
    kept = {case.plan.source_id for case in materialized if case.plan.source_id is not None}

    for row_id, row in existing.items():
        if row_id not in kept:
            await session.delete(row)
    await session.flush()

    now = _now()
    offset = len(existing) + len(materialized)
    if kept:
        await session.execute(
            update(ArenaTestCase)
            .where(ArenaTestCase.problem_id == problem.id)
            .values(ordinal=ArenaTestCase.ordinal + offset, updated_at=now)
        )
        await session.flush()

    for case in materialized:
        source_id = case.plan.source_id
        if source_id is None:
            continue
        row = existing[source_id]
        row.ordinal = case.plan.ordinal
        row.is_sample = case.plan.is_sample
        if case.plan.set_explanation:
            row.explanation = case.plan.explanation
        row.input_size_bytes = case.input_size_bytes
        row.output_size_bytes = case.output_size_bytes
        row.updated_at = now
    await session.flush()

    for case in materialized:
        if case.plan.source_id is not None:
            continue
        session.add(
            ArenaTestCase(
                id=str(uuid.uuid4()),
                problem_id=problem.id,
                ordinal=case.plan.ordinal,
                is_sample=case.plan.is_sample,
                input_size_bytes=case.input_size_bytes,
                output_size_bytes=case.output_size_bytes,
                explanation=case.plan.explanation,
                created_at=now,
                updated_at=now,
            )
        )
    await session.flush()
