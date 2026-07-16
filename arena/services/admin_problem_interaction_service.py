#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin service for sample-interaction CRUD and ordering.

Sample interactions replace sample test cases on problems with a custom
validator. Unlike test cases they live only in the database, so nothing here
returns a post-commit filesystem callback. All parsing and format rules come from
:mod:`shared.services.sample_interactions`; this module owns only the SQL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaProblem, ArenaSampleInteraction, ArenaTestCase
from shared.services.sample_interactions import MAX_SAMPLE_INTERACTIONS, interactive_testcase_violation


def _now() -> datetime:
    return datetime.now(UTC)


async def list_interactions(
    session: AsyncSession,
    problem_id: str,
    *,
    include_hidden: bool = False,
) -> list[ArenaSampleInteraction]:
    """Return a problem's sample interactions ordered by ordinal."""
    statement = select(ArenaSampleInteraction).where(ArenaSampleInteraction.problem_id == problem_id)
    if not include_hidden:
        statement = statement.where(ArenaSampleInteraction.hidden_at.is_(None))
    result = await session.execute(statement.order_by(ArenaSampleInteraction.ordinal, ArenaSampleInteraction.id))
    return list(result.scalars())


async def count_interactions(session: AsyncSession, problem_id: str) -> int:
    """Count every sample interaction of a problem, hidden ones included."""
    result = await session.execute(
        select(func.count()).select_from(ArenaSampleInteraction).where(ArenaSampleInteraction.problem_id == problem_id)
    )
    return int(result.scalar_one())


async def create_interaction(
    session: AsyncSession,
    problem: ArenaProblem,
    *,
    transcript: dict[str, object],
    explanation: str | None = None,
) -> ArenaSampleInteraction:
    """Append a sample interaction to the end of a problem's ordered list.

    Raises:
        ValueError: If the problem already holds the maximum number of
            interactions. Hidden interactions count towards the cap so a later
            un-hide can never exceed it.
    """
    existing = await count_interactions(session, problem.id)
    if existing >= MAX_SAMPLE_INTERACTIONS:
        raise ValueError(f"A problem may have at most {MAX_SAMPLE_INTERACTIONS} sample interactions.")

    now = _now()
    interaction = ArenaSampleInteraction(
        id=str(uuid.uuid4()),
        problem_id=problem.id,
        ordinal=existing + 1,
        transcript=transcript,
        explanation=explanation,
        created_at=now,
        updated_at=now,
    )
    session.add(interaction)
    return interaction


async def update_interaction(
    session: AsyncSession,
    interaction: ArenaSampleInteraction,
    *,
    transcript: dict[str, object],
    explanation: str | None = None,
) -> ArenaSampleInteraction:
    """Replace an interaction's transcript and explanation in place."""
    interaction.transcript = transcript
    interaction.explanation = explanation
    interaction.updated_at = _now()
    return interaction


async def delete_interaction(session: AsyncSession, interaction: ArenaSampleInteraction) -> None:
    """Delete one sample interaction and close the ordinal gap it leaves."""
    problem_id = interaction.problem_id
    deleted_ordinal = interaction.ordinal

    await session.delete(interaction)
    await session.flush()
    await session.execute(
        update(ArenaSampleInteraction)
        .where(
            ArenaSampleInteraction.problem_id == problem_id,
            ArenaSampleInteraction.ordinal > deleted_ordinal,
        )
        .values(ordinal=ArenaSampleInteraction.ordinal - 1, updated_at=_now())
    )


async def move_interaction(
    session: AsyncSession,
    interaction: ArenaSampleInteraction,
    new_ordinal: int,
) -> None:
    """Move a sample interaction to a new 1-based ordinal inside its problem."""
    interactions = await list_interactions(session, interaction.problem_id, include_hidden=True)
    current_index = next((index for index, item in enumerate(interactions) if item.id == interaction.id), None)
    if current_index is None:
        return

    moving = interactions.pop(current_index)
    destination_index = max(0, min(new_ordinal - 1, len(interactions)))
    interactions.insert(destination_index, moving)

    await _apply_interaction_order(session, interactions)


async def hide_interactions(session: AsyncSession, problem_id: str) -> int:
    """Hide every sample interaction of a problem and return how many were hidden."""
    result = await session.execute(
        update(ArenaSampleInteraction)
        .where(
            ArenaSampleInteraction.problem_id == problem_id,
            ArenaSampleInteraction.hidden_at.is_(None),
        )
        .values(hidden_at=_now(), updated_at=_now())
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def unhide_interactions(session: AsyncSession, problem_id: str) -> int:
    """Resurface every hidden sample interaction and return how many reappeared."""
    result = await session.execute(
        update(ArenaSampleInteraction)
        .where(
            ArenaSampleInteraction.problem_id == problem_id,
            ArenaSampleInteraction.hidden_at.is_not(None),
        )
        .values(hidden_at=None, updated_at=_now())
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def delete_all_interactions(session: AsyncSession, problem_id: str) -> int:
    """Permanently drop every sample interaction of a problem."""
    interactions = await list_interactions(session, problem_id, include_hidden=True)
    for interaction in interactions:
        await session.delete(interaction)
    await session.flush()
    return len(interactions)


async def convert_sample_testcases_to_secret(session: AsyncSession, problem_id: str) -> int:
    """Demote a problem's public test cases to secret and return how many changed.

    Called whenever a custom validator is staged: an interactive problem shows
    sample interactions, never sample test cases.
    """
    result = await session.execute(
        update(ArenaTestCase)
        .where(ArenaTestCase.problem_id == problem_id, ArenaTestCase.is_sample.is_(True))
        .values(is_sample=False, updated_at=_now())
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def interactive_testcase_error(session: AsyncSession, problem_id: str) -> str | None:
    """Return why an interactive problem's test cases are invalid, or ``None``."""
    result = await session.execute(
        select(
            func.count(),
            func.count().filter(ArenaTestCase.is_sample.is_(True)),
        ).where(ArenaTestCase.problem_id == problem_id)
    )
    total, samples = result.one()
    return interactive_testcase_violation(total_cases=int(total), sample_cases=int(samples))


async def _apply_interaction_order(session: AsyncSession, interactions: list[ArenaSampleInteraction]) -> None:
    """Persist a dense interaction order without violating ordinal uniqueness."""
    if not interactions:
        return
    now = _now()
    offset = len(interactions)
    problem_id = interactions[0].problem_id
    await session.execute(
        update(ArenaSampleInteraction)
        .where(ArenaSampleInteraction.problem_id == problem_id)
        .values(ordinal=ArenaSampleInteraction.ordinal + offset, updated_at=now)
    )
    await session.flush()
    for new_ordinal, item in enumerate(interactions, start=1):
        await session.execute(
            update(ArenaSampleInteraction)
            .where(ArenaSampleInteraction.id == item.id)
            .values(ordinal=new_ordinal, updated_at=now)
        )
    await session.flush()
