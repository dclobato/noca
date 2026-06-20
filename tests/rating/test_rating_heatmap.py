#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for compute_all_user_heatmaps and rating loop heatmap integration."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from _helpers import _make_problem, _make_user
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from rating.loops import run_user_rating_loop
from shared.db_schema.arena.arena_heatmap import arena_user_submission_heatmap
from shared.db_schema.arena.arena_submissions import arena_submissions
from shared.services.arena_heatmap import compute_all_user_heatmaps


async def _make_submission(session: AsyncSession, *, user_id: str, problem_id: str, created_at: datetime) -> None:
    """Insert a bare-minimum arena_submissions row at the given timestamp."""
    lang_id = "c17"
    from sqlalchemy import insert as _insert

    from shared.db_schema import languages

    existing = await session.scalar(select(languages.c.id).where(languages.c.id == lang_id))
    if existing is None:
        await session.execute(
            _insert(languages).values(
                id=lang_id,
                name="C17",
                icon="devicon-c",
                compile_image="noca/test:compile",
                run_image="noca/test:run",
                compile_cmd=["true"],
                run_cmd=["true"],
                source_filename="sol.c",
                artifact_path="/sandbox/sol.c",
                artifact_is_source=True,
                compile_timeout_s=10.0,
                active=True,
            )
        )
    await session.execute(
        insert(arena_submissions).values(
            id=str(uuid.uuid4()),
            user_id=user_id,
            problem_id=problem_id,
            language_id=lang_id,
            source_code="int main(){}",
            source_hash="a" * 64,
            source_size_bytes=12,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# compute_all_user_heatmaps — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heatmap_empty_when_no_submissions(session: AsyncSession) -> None:
    """No submissions → returns 0 and inserts no rows."""
    count = await compute_all_user_heatmaps(session)
    assert count == 0
    rows = (await session.execute(select(arena_user_submission_heatmap))).fetchall()
    assert rows == []


@pytest.mark.asyncio
async def test_heatmap_single_user_single_day(session: AsyncSession) -> None:
    """One submission produces one heatmap row with one data entry."""
    user = await _make_user(session)
    author = await _make_user(session)
    problem = await _make_problem(session, author)
    ts = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    await _make_submission(session, user_id=user.id, problem_id=problem.id, created_at=ts)

    count = await compute_all_user_heatmaps(session)
    assert count == 1
    row = (
        (
            await session.execute(
                select(
                    arena_user_submission_heatmap.c.data,
                    arena_user_submission_heatmap.c.range_start,
                    arena_user_submission_heatmap.c.range_end,
                ).where(arena_user_submission_heatmap.c.user_id == user.id)
            )
        )
        .mappings()
        .one()
    )

    assert row["data"] == [["2026-01-15", 1]]
    assert row["range_start"] < "2026-01-15"
    assert row["range_end"] >= "2026-01-15"


@pytest.mark.asyncio
async def test_heatmap_multiple_submissions_same_day_are_summed(session: AsyncSession) -> None:
    """Several submissions on the same day produce a single entry with their sum."""
    user = await _make_user(session)
    author = await _make_user(session)
    problem = await _make_problem(session, author)
    day = datetime(2026, 3, 1, tzinfo=UTC)
    for offset_hours in (0, 3, 7, 14):
        await _make_submission(
            session, user_id=user.id, problem_id=problem.id, created_at=day + timedelta(hours=offset_hours)
        )

    await compute_all_user_heatmaps(session)
    row = (
        await session.execute(
            select(arena_user_submission_heatmap.c.data).where(arena_user_submission_heatmap.c.user_id == user.id)
        )
    ).scalar_one()
    assert row == [["2026-03-01", 4]]


@pytest.mark.asyncio
async def test_heatmap_zero_count_days_absent(session: AsyncSession) -> None:
    """Days with no submissions must not appear in the stored JSON."""
    user = await _make_user(session)
    author = await _make_user(session)
    problem = await _make_problem(session, author)
    t1 = datetime(2026, 4, 1, tzinfo=UTC)
    t2 = datetime(2026, 4, 5, tzinfo=UTC)
    await _make_submission(session, user_id=user.id, problem_id=problem.id, created_at=t1)
    await _make_submission(session, user_id=user.id, problem_id=problem.id, created_at=t2)

    await compute_all_user_heatmaps(session)
    data = (
        await session.execute(
            select(arena_user_submission_heatmap.c.data).where(arena_user_submission_heatmap.c.user_id == user.id)
        )
    ).scalar_one()
    dates = [entry[0] for entry in data]
    assert dates == ["2026-04-01", "2026-04-05"]


@pytest.mark.asyncio
async def test_heatmap_utc_day_boundary(session: AsyncSession) -> None:
    """A submission at 23:59:59 UTC on day D counts on D, not D+1."""
    user = await _make_user(session)
    author = await _make_user(session)
    problem = await _make_problem(session, author)
    last_second_of_day = datetime(2026, 5, 10, 23, 59, 59, tzinfo=UTC)
    await _make_submission(session, user_id=user.id, problem_id=problem.id, created_at=last_second_of_day)

    await compute_all_user_heatmaps(session)
    data = (
        await session.execute(
            select(arena_user_submission_heatmap.c.data).where(arena_user_submission_heatmap.c.user_id == user.id)
        )
    ).scalar_one()
    assert len(data) == 1
    assert data[0][0] == "2026-05-10"


@pytest.mark.asyncio
async def test_heatmap_window_bounds(session: AsyncSession) -> None:
    """Submission at window_start is included; one second before is excluded."""

    user = await _make_user(session)
    author = await _make_user(session)
    problem = await _make_problem(session, author)

    today = datetime.now(UTC).date()
    range_start = today - timedelta(days=363)
    window_start = datetime(range_start.year, range_start.month, range_start.day, tzinfo=UTC)

    # Exactly at window start — must be included
    await _make_submission(session, user_id=user.id, problem_id=problem.id, created_at=window_start)
    # One second before window start — must be excluded
    await _make_submission(
        session,
        user_id=user.id,
        problem_id=problem.id,
        created_at=window_start - timedelta(seconds=1),
    )

    await compute_all_user_heatmaps(session)
    data = (
        await session.execute(
            select(arena_user_submission_heatmap.c.data).where(arena_user_submission_heatmap.c.user_id == user.id)
        )
    ).scalar_one()
    assert len(data) == 1
    assert data[0][0] == range_start.isoformat()


@pytest.mark.asyncio
async def test_heatmap_stale_row_replaced(session: AsyncSession) -> None:
    """A second call overwrites the old snapshot; range dates are refreshed."""
    user = await _make_user(session)
    author = await _make_user(session)
    problem = await _make_problem(session, author)

    ts = datetime.now(UTC) - timedelta(days=10)
    await _make_submission(session, user_id=user.id, problem_id=problem.id, created_at=ts)

    await compute_all_user_heatmaps(session)
    first_end = (
        await session.execute(
            select(arena_user_submission_heatmap.c.range_end).where(arena_user_submission_heatmap.c.user_id == user.id)
        )
    ).scalar_one()

    # Call again — should succeed and replace the row
    await compute_all_user_heatmaps(session)
    second_end = (
        await session.execute(
            select(arena_user_submission_heatmap.c.range_end).where(arena_user_submission_heatmap.c.user_id == user.id)
        )
    ).scalar_one()
    assert second_end >= first_end
    # Only one row per user
    count = (
        await session.execute(
            select(arena_user_submission_heatmap).where(arena_user_submission_heatmap.c.user_id == user.id)
        )
    ).fetchall()
    assert len(count) == 1


@pytest.mark.asyncio
async def test_heatmap_inactive_user_snapshot_deleted_on_next_cycle(session: AsyncSession) -> None:
    """A user whose submissions age out of the window has their row deleted on the next cycle."""
    user = await _make_user(session)

    # Directly insert a stale snapshot as if a previous cycle had run
    await session.execute(
        insert(arena_user_submission_heatmap).values(
            user_id=user.id,
            data=[["2025-01-01", 2]],
            range_start="2024-01-02",
            range_end="2025-01-01",
            computed_at=datetime.now(UTC) - timedelta(days=400),
        )
    )
    await session.flush()

    # No submissions in the current window → the row must be removed
    count = await compute_all_user_heatmaps(session)
    assert count == 0
    remaining = (
        await session.execute(
            select(arena_user_submission_heatmap).where(arena_user_submission_heatmap.c.user_id == user.id)
        )
    ).fetchall()
    assert remaining == []


@pytest.mark.asyncio
async def test_heatmap_caller_owns_commit(engine, session: AsyncSession) -> None:
    """Service writes are pending until caller commits; not visible before then."""
    from sqlalchemy.ext.asyncio import AsyncSession as _AS
    from sqlalchemy.ext.asyncio import async_sessionmaker

    user = await _make_user(session)
    author = await _make_user(session)
    problem = await _make_problem(session, author)
    ts = datetime.now(UTC) - timedelta(days=5)
    await _make_submission(session, user_id=user.id, problem_id=problem.id, created_at=ts)
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Run service in session A without committing
    async with factory() as session_a:
        await compute_all_user_heatmaps(session_a)
        # Before commit: not visible in a separate session
        async with factory() as session_b:
            session_b: _AS
            row = (
                await session_b.execute(
                    select(arena_user_submission_heatmap).where(arena_user_submission_heatmap.c.user_id == user.id)
                )
            ).fetchall()
            assert row == [], "Row should not be visible before caller commits"
        await session_a.commit()

    # After commit: visible
    async with factory() as session_c:
        session_c: _AS
        row = (
            await session_c.execute(
                select(arena_user_submission_heatmap).where(arena_user_submission_heatmap.c.user_id == user.id)
            )
        ).fetchall()
        assert len(row) == 1


# ---------------------------------------------------------------------------
# run_user_rating_loop — heatmap integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heatmap_failure_does_not_block_user_done(engine, session: AsyncSession) -> None:
    """Heatmap exception must not prevent user_done from being set."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    await _make_user(session)
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    stop = asyncio.Event()
    problem_done = asyncio.Event()
    user_done = asyncio.Event()
    logger = __import__("logging").getLogger("test")

    with patch(
        "rating.loops.compute_all_user_heatmaps",
        new_callable=AsyncMock,
        side_effect=RuntimeError("heatmap boom"),
    ):
        problem_done.set()
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
        for _ in range(50):
            if user_done.is_set():
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("user_done was not set despite heatmap failure")
        stop.set()
        problem_done.set()
        await task


@pytest.mark.asyncio
async def test_heatmap_committed_by_loop(engine, session: AsyncSession) -> None:
    """Successful heatmap cycle via run_user_rating_loop persists rows."""
    from sqlalchemy.ext.asyncio import AsyncSession as _AS
    from sqlalchemy.ext.asyncio import async_sessionmaker

    user = await _make_user(session)
    author = await _make_user(session)
    problem = await _make_problem(session, author)
    ts = datetime.now(UTC) - timedelta(days=2)
    await _make_submission(session, user_id=user.id, problem_id=problem.id, created_at=ts)
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    stop = asyncio.Event()
    problem_done = asyncio.Event()
    user_done = asyncio.Event()
    logger = __import__("logging").getLogger("test")

    problem_done.set()
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
    for _ in range(50):
        if user_done.is_set():
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("user_done was not set in time")
    stop.set()
    problem_done.set()
    await task

    async with factory() as check:
        check: _AS
        row = (
            await check.execute(
                select(arena_user_submission_heatmap).where(arena_user_submission_heatmap.c.user_id == user.id)
            )
        ).fetchall()
    assert len(row) == 1
