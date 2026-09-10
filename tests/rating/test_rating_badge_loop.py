#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for badge config validation and the rating badge-assignment loop."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rating.loops import run_badge_assignment_loop
from shared.db_schema.arena import (
    arena_badge_cycle_state,
    arena_submission_judgments,
    arena_submissions,
    arena_user_badges,
    arena_users,
)
from shared.db_schema.arena.arena_badge_cycle_state import BADGE_CYCLE_STATE_ID
from shared.enumerations import ArenaBadge, ArenaRole, JudgmentStatus, Verdict


def test_badge_interval_boundary_validation() -> None:
    """NOCA_RATING_BADGE_INTERVAL must reject values outside [900, 604800]."""
    import os

    from rating.config import Settings

    for bad in ("899", "604801"):
        os.environ["NOCA_RATING_BADGE_INTERVAL"] = bad
        try:
            with pytest.raises((ValidationError, ValueError)):
                Settings(_env_file=None)
        finally:
            del os.environ["NOCA_RATING_BADGE_INTERVAL"]


def test_badge_config_defaults() -> None:
    """The badge loop defaults to 15-minute cadence and daily reconciliation."""
    from rating.config import Settings

    settings = Settings(_env_file=None)
    assert settings.BADGE_INTERVAL == 900
    assert settings.BADGE_LOOKBACK_SECONDS == 600
    assert settings.BADGE_RECONCILE_INTERVAL == 86400


@pytest.mark.asyncio
async def test_badge_loop_awards_on_startup(engine, session: AsyncSession) -> None:
    """run_badge_assignment_loop runs a full reconcile immediately and awards badges."""
    user_id = str(uuid.uuid4())
    submission_id = str(uuid.uuid4())
    when = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    await session.execute(
        arena_users.insert().values(
            id=user_id,
            nome="Loop User",
            email_normalizado=f"{user_id}@test.example",
            password_hash="x",
            role=ArenaRole.ARENA_USER,
        )
    )
    await session.execute(
        arena_submissions.insert().values(
            id=submission_id,
            user_id=user_id,
            problem_id=str(uuid.uuid4()),
            language_id="gcc-c17",
            source_code="x",
            source_hash=submission_id,
            source_size_bytes=1,
            created_at=when,
        )
    )
    await session.execute(
        arena_submission_judgments.insert().values(
            id=str(uuid.uuid4()),
            submission_id=submission_id,
            status=JudgmentStatus.DONE.value,
            final_verdict=Verdict.AC.value,
            finished_at=when,
            created_at=when,
        )
    )
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_badge_assignment_loop(
            session_factory=factory,
            interval_seconds=3600,  # would block without run_immediately
            stop_event=stop,
            logger=logging.getLogger("test-badge"),
            run_immediately=True,
        )
    )
    try:
        for _ in range(50):
            async with factory() as check:
                held = (
                    (
                        await check.execute(
                            select(arena_user_badges.c.badge).where(arena_user_badges.c.user_id == user_id)
                        )
                    )
                    .scalars()
                    .all()
                )
            if ArenaBadge.HELLO_WORLD.value in held:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("badge loop did not award HELLO_WORLD")
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_badge_loop_startup_cycle_revokes_a_badge_nothing_earns(engine, session: AsyncSession) -> None:
    """The startup cycle reconciles fully, so a badge nothing earns is taken back.

    This is the deploy case: the ledger holds a badge that the award rules in
    the running image no longer derive. An incremental pass is bounded by the
    watermark, sees no events at all here, and would leave the row standing
    until the reconcile interval came due.
    """
    user_id = str(uuid.uuid4())
    submission_id = str(uuid.uuid4())
    when = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    await session.execute(
        arena_users.insert().values(
            id=user_id,
            nome="Backfill User",
            email_normalizado=f"{user_id}@test.example",
            password_hash="x",
            role=ArenaRole.ARENA_USER,
        )
    )
    await session.execute(
        arena_submissions.insert().values(
            id=submission_id,
            user_id=user_id,
            problem_id=str(uuid.uuid4()),
            language_id="gcc-c17",
            source_code="x",
            source_hash=submission_id,
            source_size_bytes=1,
            created_at=when,
        )
    )
    await session.execute(
        arena_submission_judgments.insert().values(
            id=str(uuid.uuid4()),
            submission_id=submission_id,
            status=JudgmentStatus.DONE.value,
            final_verdict=Verdict.WA.value,
            finished_at=when,
            created_at=when,
        )
    )
    # A held badge anchored to a submission that is not Accepted: nothing in the
    # live corpus earns it, so a full pass must revoke it.
    await session.execute(
        arena_user_badges.insert().values(
            id=str(uuid.uuid4()),
            user_id=user_id,
            badge=ArenaBadge.HELLO_WORLD.value,
            awarded_at=when,
            submission_id=submission_id,
        )
    )
    # A watermark past the submission, so an incremental pass would see no events.
    await session.execute(
        arena_badge_cycle_state.insert().values(
            id=BADGE_CYCLE_STATE_ID,
            last_processed_at=when + timedelta(days=1),
            last_reconciled_at=when + timedelta(days=1),
            updated_at=when + timedelta(days=1),
        )
    )
    await session.commit()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_badge_assignment_loop(
            session_factory=factory,
            interval_seconds=3600,
            stop_event=stop,
            logger=logging.getLogger("test-badge"),
            run_immediately=True,
        )
    )
    try:
        for _ in range(50):
            async with factory() as check:
                held = (
                    await check.execute(
                        select(arena_user_badges.c.id).where(
                            arena_user_badges.c.user_id == user_id,
                            arena_user_badges.c.badge == ArenaBadge.HELLO_WORLD.value,
                        )
                    )
                ).first()
            if held is None:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("startup cycle did not revoke the unearned badge")
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)
