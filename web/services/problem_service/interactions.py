#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Sample-interaction persistence for Contest problems.

Sample interactions replace sample test cases on problems with a custom
validator. They live only in the database (no filesystem twin), so unlike test
cases their reordering and removal need no post-commit file callbacks. All
parsing and format rules come from
:mod:`shared.services.sample_interactions`; this module owns only the SQL.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.sample_interactions import MAX_SAMPLE_INTERACTIONS, interactive_testcase_violation
from web.models.problem import Problem, ProblemSampleInteraction, ProblemTestCase

from .ordering import apply_dense_ordinals, bulk_update_ordinals, clamp_ordinal

_TABLE = "problem_sample_interactions"


async def load_sample_interactions(
    session: AsyncSession,
    problem_id: str,
    *,
    include_hidden: bool = False,
) -> list[ProblemSampleInteraction]:
    """Load a problem's sample interactions in stable ordinal order."""
    statement = select(ProblemSampleInteraction).where(ProblemSampleInteraction.problem_id == problem_id)
    if not include_hidden:
        statement = statement.where(ProblemSampleInteraction.hidden_at.is_(None))
    result = await session.execute(statement.order_by(ProblemSampleInteraction.ordinal, ProblemSampleInteraction.id))
    return list(result.scalars().all())


async def count_sample_interactions(session: AsyncSession, problem_id: str) -> int:
    """Count every sample interaction of a problem, hidden ones included."""
    result = await session.execute(
        select(func.count())
        .select_from(ProblemSampleInteraction)
        .where(ProblemSampleInteraction.problem_id == problem_id)
    )
    return int(result.scalar_one())


async def append_sample_interaction(
    session: AsyncSession,
    problem: Problem,
    *,
    transcript: dict[str, object],
    explanation: str | None,
) -> ProblemSampleInteraction:
    """Append a sample interaction to the end of a problem's ordered list.

    Raises:
        ValueError: If the problem already holds the maximum number of
            interactions. Hidden interactions count towards the cap so a later
            un-hide can never exceed it.
    """
    existing = await count_sample_interactions(session, problem.id)
    if existing >= MAX_SAMPLE_INTERACTIONS:
        raise ValueError(f"A problem may have at most {MAX_SAMPLE_INTERACTIONS} sample interactions.")

    interaction = ProblemSampleInteraction(
        problem_id=problem.id,
        ordinal=existing + 1,
        transcript=transcript,
        explanation=explanation,
    )
    session.add(interaction)
    await session.flush()
    return interaction


async def update_sample_interaction(
    interaction: ProblemSampleInteraction,
    *,
    transcript: dict[str, object],
    explanation: str | None,
) -> None:
    """Replace an interaction's transcript and explanation in place."""
    interaction.transcript = transcript
    interaction.explanation = explanation


async def move_sample_interaction(
    session: AsyncSession,
    problem: Problem,
    interaction: ProblemSampleInteraction,
    new_ordinal: int,
) -> None:
    """Move a sample interaction to a new 1-based position inside its problem."""
    if interaction.problem_id != problem.id:
        raise ValueError("Sample interaction does not belong to the provided problem.")

    interactions = await load_sample_interactions(session, problem.id, include_hidden=True)
    current_index = next((index for index, item in enumerate(interactions) if item.id == interaction.id), None)
    if current_index is None:
        raise ValueError("Sample interaction was not found in the provided problem.")

    moving = interactions.pop(current_index)
    destination_index = clamp_ordinal(new_ordinal, size=len(interactions) + 1) - 1
    interactions.insert(destination_index, moving)

    apply_dense_ordinals(interactions)
    await bulk_update_ordinals(session, _TABLE, interactions)


async def remove_sample_interaction_and_resequence(
    session: AsyncSession,
    problem: Problem,
    interaction: ProblemSampleInteraction,
) -> None:
    """Remove a sample interaction from a problem and close ordinal gaps."""
    if interaction.problem_id != problem.id:
        raise ValueError("Sample interaction does not belong to the provided problem.")

    interactions = await load_sample_interactions(session, problem.id, include_hidden=True)
    remaining = [item for item in interactions if item.id != interaction.id]

    # Delete the removed row first so it can never collide with the temp ordinals
    # used while resequencing the survivors, then close the gap densely.
    await session.delete(interaction)
    await session.flush()
    apply_dense_ordinals(remaining)
    await bulk_update_ordinals(session, _TABLE, remaining)


async def hide_sample_interactions(session: AsyncSession, problem_id: str) -> int:
    """Hide every sample interaction of a problem and return how many were hidden."""
    result = await session.execute(
        update(ProblemSampleInteraction)
        .where(
            ProblemSampleInteraction.problem_id == problem_id,
            ProblemSampleInteraction.hidden_at.is_(None),
        )
        .values(hidden_at=datetime.now(UTC))
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def unhide_sample_interactions(session: AsyncSession, problem_id: str) -> int:
    """Resurface every hidden sample interaction and return how many reappeared."""
    result = await session.execute(
        update(ProblemSampleInteraction)
        .where(
            ProblemSampleInteraction.problem_id == problem_id,
            ProblemSampleInteraction.hidden_at.is_not(None),
        )
        .values(hidden_at=None)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def delete_sample_interactions(session: AsyncSession, problem_id: str) -> int:
    """Permanently drop every sample interaction of a problem."""
    interactions = await load_sample_interactions(session, problem_id, include_hidden=True)
    for interaction in interactions:
        await session.delete(interaction)
    await session.flush()
    return len(interactions)


async def convert_sample_test_cases_to_secret(session: AsyncSession, problem_id: str) -> int:
    """Demote a problem's public test cases to secret and return how many changed.

    Called whenever a custom validator is staged: an interactive problem shows
    sample interactions, never sample test cases.
    """
    result = await session.execute(
        update(ProblemTestCase)
        .where(ProblemTestCase.problem_id == problem_id, ProblemTestCase.is_sample.is_(True))
        .values(is_sample=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def interactive_testcase_error(session: AsyncSession, problem_id: str) -> str | None:
    """Return why an interactive problem's test cases are invalid, or ``None``."""
    result = await session.execute(
        select(
            func.count(),
            func.count().filter(ProblemTestCase.is_sample.is_(True)),
        ).where(ProblemTestCase.problem_id == problem_id)
    )
    total, samples = result.one()
    return interactive_testcase_violation(total_cases=int(total), sample_cases=int(samples))
