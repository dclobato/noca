#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The cache key for a problem's contestant-facing package.

``problems.public_export_generation`` answers one question: *is a cached public
export still the current one?* A cached ZIP carries the counter value it was
built from in its sidecar, and it is served only while that value still equals
the row's. Reading the row per request is what lets any replica detect a stale
file of its own without cross-replica invalidation.

It lives in its own module, apart from
:func:`shared.services.problem_package.edit_swap.bump_artifact_generation`,
because the two counters must never be conflated. ``artifact_generation`` is a
**crash-recovery fence**: recovery treats ``stored >= expected`` as proof that a
Save's filesystem promotion committed. That inference only holds while every
bump corresponds to a real filesystem Save. The bump runs inside the caller's
open transaction, so a Save that crashes before commit rolls back, releases the
problem row's lock, and reverts the value -- letting a transaction that was
blocked behind it compute the very integer the crashed Save's journal recorded
as its own expected value. Recovery cannot tell the two apart, and would keep
promoted artifacts whose database changes never landed.

Nothing downstream ever infers filesystem-commit state from the counter here, so
the same coincidence costs one stale cache read, corrected by the next bump.
That is why cache invalidation gets a counter of its own rather than borrowing
the fence, and why this module deliberately does not import from ``edit_swap``.
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import arena_problems as _arena_problems
from shared.db_schema import problems as _problems
from shared.services.problem_package.promotion import ImportDomain

__all__ = ["bump_public_export_generation"]


async def bump_public_export_generation(session: AsyncSession, domain: ImportDomain, problem_id: str) -> int:
    """Invalidate a problem's cached public export, inside the caller's transaction.

    A plain atomic increment: no journal, no swap, and no filesystem ordering to
    respect, because the only thing that reads it is a cache that rebuilds on a
    mismatch. Call it in the same transaction as the change it describes, so a
    rolled-back edit cannot invalidate a cache that is still correct.

    The column is a ``BigInteger``, so the counter runs to 2**63 - 1. That bound
    is not reachable -- one increment per save of one problem, so even a save
    every second exhausts it in ~292 billion years -- but the behaviour *at* it
    is worth knowing, because it is the safe one: PostgreSQL raises
    ``NumericValueOutOfRangeError`` rather than wrapping, and since this runs
    inside the caller's transaction the save is refused. A wrap would be far
    worse than a refused edit: a negative value could coincide with what a
    sidecar already records, pinning a stale package as current indefinitely.

    Args:
        session: The session owning the mutation's transaction.
        domain: Which problem table to update.
        problem_id: The problem whose public package changed.

    Returns:
        The new generation.

    Raises:
        ValueError: If the problem row does not exist.
    """
    table = _arena_problems if domain == "arena" else _problems
    statement = (
        update(table)
        .where(table.c.id == problem_id)
        .values(public_export_generation=table.c.public_export_generation + 1)
        .returning(table.c.public_export_generation)
    )
    generation = await session.scalar(statement)
    if generation is None:
        raise ValueError(f"Cannot bump the public export generation of an unknown problem: {problem_id!r}")
    return int(generation)
