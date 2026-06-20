#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena rating worker loop coordination."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from _helpers import _make_problem, _make_user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rating.loops import run_problem_rating_loop, run_user_rating_loop
from shared.db_schema.arena import arena_problem_ratings, arena_users


@pytest.mark.asyncio
async def test_startup_rating_loops_run_without_interval_wait(engine, session: AsyncSession) -> None:
    """Startup rating behavior must update problems and users without waiting an interval."""
    from sqlalchemy.ext.asyncio import AsyncSession as _AS
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)

    user = await _make_user(session)
    problem = await _make_problem(session, user)
    await session.commit()

    stop = asyncio.Event()
    problem_done = asyncio.Event()
    user_done = asyncio.Event()
    logger = __import__("logging").getLogger("test")
    published_updates: list[datetime | None] = []

    problem_task = asyncio.create_task(
        run_problem_rating_loop(
            session_factory=factory,
            interval_seconds=3600,  # would block for 1 h without run_immediately
            stop_event=stop,
            logger=logger,
            problem_done=problem_done,
            run_immediately=True,
            next_update_callback=published_updates.append,
        )
    )
    user_task = asyncio.create_task(
        run_user_rating_loop(
            session_factory=factory,
            interval_seconds=3600,
            stop_event=stop,
            logger=logger,
            problem_done=problem_done,
            user_done=user_done,
        )
    )

    # The problem cycle should run immediately and signal the user cycle.
    for _ in range(50):
        async with factory() as check_session:
            check_session: _AS
            problem_updated = await check_session.scalar(
                select(arena_problem_ratings.c.dta_rating_update).where(
                    arena_problem_ratings.c.problem_id == problem.id
                )
            )
            user_updated = await check_session.scalar(
                select(arena_users.c.dta_rating_update).where(arena_users.c.id == user.id)
            )
        if problem_updated is not None and user_updated is not None:
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("startup rating loops did not update problems and users without interval wait")

    stop.set()
    problem_done.set()  # unblock user loop if it is already waiting for the next cycle
    user_done.set()
    await asyncio.gather(problem_task, user_task)
    assert any(update is not None for update in published_updates)


@pytest.mark.asyncio
async def test_user_rating_loop_waits_for_problem_done(engine, session: AsyncSession) -> None:
    """User loop must not run until problem_done is set."""
    from sqlalchemy.ext.asyncio import AsyncSession as _AS
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)

    user = await _make_user(session)
    await session.commit()

    stop = asyncio.Event()
    problem_done = asyncio.Event()
    user_done = asyncio.Event()
    logger = __import__("logging").getLogger("test")

    task = asyncio.create_task(
        run_user_rating_loop(
            session_factory=factory,
            interval_seconds=3600,
            stop_event=stop,
            logger=logger,
            problem_done=problem_done,
            user_done=user_done,
        )
    )

    # Give the loop a moment to reach the wait point
    await asyncio.sleep(0.05)

    # User rating should NOT have been updated yet (loop is blocked on problem_done)
    async with factory() as check_session:
        check_session: _AS
        row_before = await check_session.scalar(
            select(arena_users.c.dta_rating_update).where(arena_users.c.id == user.id)
        )
    assert row_before is None

    # Trigger the user loop
    problem_done.set()
    await asyncio.sleep(0.2)

    # Now stop
    stop.set()
    problem_done.set()  # unblock if still waiting
    await task

    # Verify dta_rating_update was written via a fresh session
    async with factory() as verify_session:
        verify_session: _AS
        row_after = await verify_session.scalar(
            select(arena_users.c.dta_rating_update).where(arena_users.c.id == user.id)
        )
    assert row_after is not None


@pytest.mark.asyncio
async def test_problem_rating_loop_calls_on_cycle_start(engine) -> None:
    """on_cycle_start is awaited at the start of each rating cycle."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    stop = asyncio.Event()
    problem_done = asyncio.Event()
    logger = __import__("logging").getLogger("test")
    cycle_starts: list[int] = []
    call_count = 0

    async def _on_start() -> None:
        nonlocal call_count
        call_count += 1
        cycle_starts.append(call_count)
        stop.set()

    task = asyncio.create_task(
        run_problem_rating_loop(
            session_factory=factory,
            interval_seconds=3600,
            stop_event=stop,
            logger=logger,
            problem_done=problem_done,
            run_immediately=True,
            on_cycle_start=_on_start,
        )
    )
    await asyncio.wait_for(task, timeout=5)

    assert cycle_starts == [1]


@pytest.mark.asyncio
async def test_problem_rating_loop_on_cycle_start_exception_does_not_stop_loop(engine) -> None:
    """An exception in on_cycle_start is swallowed so the rating loop continues."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    stop = asyncio.Event()
    problem_done = asyncio.Event()
    logger = __import__("logging").getLogger("test")
    cycle_count = 0

    async def _failing_on_start() -> None:
        nonlocal cycle_count
        cycle_count += 1
        if cycle_count >= 1:
            stop.set()
        raise RuntimeError("simulated publish failure")

    task = asyncio.create_task(
        run_problem_rating_loop(
            session_factory=factory,
            interval_seconds=3600,
            stop_event=stop,
            logger=logger,
            problem_done=problem_done,
            run_immediately=True,
            on_cycle_start=_failing_on_start,
        )
    )
    # The loop must complete despite the callback raising.
    await asyncio.wait_for(task, timeout=5)
    assert cycle_count >= 1
