#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Data-access layer for the authoritative Arena worker pause state.

PostgreSQL is the single source of truth for whether a worker is paused. The
arena route bumps the state inside its own transaction (commit before any Valkey
nudge); each worker reconciles its live flag from the committed rows here. The
monotonic ``generation`` column makes a replayed Valkey command a no-op once the
worker has already adopted that generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from shared.db_schema.arena import arena_worker_pause_state


@dataclass(frozen=True, slots=True)
class PauseStateRow:
    """A committed pause-state row for one worker."""

    worker_class: str
    worker_id: str
    paused: bool
    paused_by: str | None
    generation: int


# Accepts an AsyncSession (arena) or AsyncConnection (workers); both expose
# ``execute``. Typed loosely to avoid importing concrete session types here.
_Executor = object


async def read_worker_pause_state(
    executor: _Executor,
    worker_class: str,
    worker_id: str,
) -> PauseStateRow | None:
    """Return the committed pause-state row for a worker, or None when absent.

    Args:
        executor: AsyncSession or AsyncConnection with an ``execute`` method.
        worker_class: WorkerClass value.
        worker_id: Stable worker identifier.

    Returns:
        The committed ``PauseStateRow`` or None when no row exists yet.
    """
    stmt = select(
        arena_worker_pause_state.c.worker_class,
        arena_worker_pause_state.c.worker_id,
        arena_worker_pause_state.c.paused,
        arena_worker_pause_state.c.paused_by,
        arena_worker_pause_state.c.generation,
    ).where(
        arena_worker_pause_state.c.worker_class == worker_class,
        arena_worker_pause_state.c.worker_id == worker_id,
    )
    result = await executor.execute(stmt)  # type: ignore[attr-defined]
    row = result.first()
    if row is None:
        return None
    return PauseStateRow(
        worker_class=row.worker_class,
        worker_id=row.worker_id,
        paused=bool(row.paused),
        paused_by=row.paused_by,
        generation=int(row.generation),
    )


async def bump_worker_pause_state(
    executor: _Executor,
    *,
    worker_class: str,
    worker_id: str,
    paused: bool,
    paused_by: str | None,
) -> int:
    """Atomically upsert the pause state and return the new generation.

    On first write the generation is 1; every subsequent write increments it,
    keeping it strictly monotonic per worker. The caller owns the surrounding
    transaction and must commit before publishing any Valkey nudge.

    Args:
        executor: AsyncSession or AsyncConnection with an ``execute`` method.
        worker_class: WorkerClass value.
        worker_id: Stable worker identifier.
        paused: Desired paused state.
        paused_by: Email of the admin performing the change.

    Returns:
        The new monotonic generation committed for this worker.
    """
    insert = sqlite_insert if _dialect_name(executor) == "sqlite" else pg_insert
    stmt = (
        insert(arena_worker_pause_state)
        .values(
            worker_class=worker_class,
            worker_id=worker_id,
            paused=paused,
            paused_by=paused_by,
            generation=1,
            updated_at=datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=["worker_class", "worker_id"],
            set_={
                "paused": paused,
                "paused_by": paused_by,
                "generation": arena_worker_pause_state.c.generation + 1,
                "updated_at": datetime.now(UTC),
            },
        )
        .returning(arena_worker_pause_state.c.generation)
    )
    result = await executor.execute(stmt)  # type: ignore[attr-defined]
    return int(result.scalar_one())


def _dialect_name(executor: _Executor) -> str:
    """Return the SQL dialect name for an AsyncSession or AsyncConnection."""
    bind = getattr(executor, "bind", None)
    if bind is not None:
        return str(bind.dialect.name)
    dialect = getattr(executor, "dialect", None)
    if dialect is not None:
        return str(dialect.name)
    engine = getattr(executor, "engine", None)
    if engine is not None:
        return str(engine.dialect.name)
    return "postgresql"
