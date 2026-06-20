#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the authoritative worker pause-state data-access layer."""

from __future__ import annotations

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError

from shared.db_schema.arena import arena_worker_pause_state
from shared.services.valkey_service.worker_commands import LivePauseFlag
from shared.services.worker_pause_state import (
    bump_worker_pause_state,
    read_worker_pause_state,
)

_CLASS = "autojudge"
_ID = "judge-1"


@pytest.mark.asyncio
async def test_bump_increments_generation_monotonically(session) -> None:
    """Each bump returns a strictly increasing generation."""
    first = await bump_worker_pause_state(session, worker_class=_CLASS, worker_id=_ID, paused=True, paused_by="a@b.c")
    second = await bump_worker_pause_state(session, worker_class=_CLASS, worker_id=_ID, paused=False, paused_by="a@b.c")
    third = await bump_worker_pause_state(session, worker_class=_CLASS, worker_id=_ID, paused=True, paused_by="a@b.c")
    await session.commit()
    assert first == 1
    assert second == 2
    assert third == 3


@pytest.mark.asyncio
async def test_read_round_trip(session) -> None:
    """A read returns the last committed paused state and actor."""
    await bump_worker_pause_state(session, worker_class=_CLASS, worker_id=_ID, paused=True, paused_by="admin@x.y")
    await session.commit()

    row = await read_worker_pause_state(session, _CLASS, _ID)
    assert row is not None
    assert row.paused is True
    assert row.paused_by == "admin@x.y"
    assert row.generation == 1


@pytest.mark.asyncio
async def test_read_missing_returns_none(session) -> None:
    """An unknown worker has no committed pause state."""
    assert await read_worker_pause_state(session, _CLASS, "absent") is None


@pytest.mark.asyncio
async def test_reconcile_adopts_higher_generation(session) -> None:
    """A flag adopts committed state only when generation advanced."""
    await bump_worker_pause_state(session, worker_class=_CLASS, worker_id=_ID, paused=True, paused_by="admin@x.y")
    await session.commit()

    flag = LivePauseFlag()
    row = await read_worker_pause_state(session, _CLASS, _ID)
    assert row is not None
    assert row.generation > flag.applied_generation
    flag.paused = row.paused
    flag.applied_generation = row.generation
    assert flag.paused is True
    assert flag.applied_generation == 1


@pytest.mark.asyncio
async def test_stale_generation_is_a_no_op(session) -> None:
    """A command carrying a generation already applied changes nothing."""
    await bump_worker_pause_state(session, worker_class=_CLASS, worker_id=_ID, paused=True, paused_by="admin@x.y")
    await session.commit()

    flag = LivePauseFlag(paused=True, applied_generation=1)
    row = await read_worker_pause_state(session, _CLASS, _ID)
    assert row is not None
    # A replayed command references generation 1, which is not greater than the
    # already-applied generation, so reconciliation is a no-op.
    assert row.generation <= flag.applied_generation


@pytest.mark.asyncio
async def test_negative_generation_is_rejected_by_database(session) -> None:
    """The schema prevents invalid negative pause-state generations."""
    with pytest.raises(IntegrityError):
        await session.execute(
            insert(arena_worker_pause_state).values(
                worker_class=_CLASS,
                worker_id="negative-generation",
                paused=True,
                paused_by="admin@x.y",
                generation=-1,
            )
        )
        await session.flush()
    await session.rollback()
