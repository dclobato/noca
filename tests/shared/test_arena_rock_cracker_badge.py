#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Lifecycle tests for the dynamic Arena ROCK_CRACKER badge."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Row, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import (
    arena_badge_cycle_state,
    arena_problem_ratings,
    arena_problem_solvers,
    arena_problems,
    arena_submission_judgments,
    arena_submissions,
    arena_user_badges,
    arena_users,
)
from shared.enumerations import ArenaBadge, JudgmentStatus, ProblemValidatorType, Verdict
from shared.services.arena_badges import compute_badge_awards

pytestmark = pytest.mark.asyncio

_START = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)


async def _new_user(session: AsyncSession) -> str:
    """Insert a minimal Arena user and return its id."""
    user_id = str(uuid.uuid4())
    await session.execute(
        arena_users.insert().values(
            id=user_id,
            nome="Rock Cracker User",
            email_normalizado=f"{user_id}@test.example",
            password_hash="x",
        )
    )
    return user_id


async def _new_problem(session: AsyncSession, owner_id: str) -> str:
    """Insert a minimal Arena problem and return its id."""
    problem_id = str(uuid.uuid4())
    await session.execute(
        arena_problems.insert().values(
            id=problem_id,
            arena_number=int(uuid.uuid4().int % 1_000_000_000) + 1,
            title=f"Rock Cracker {uuid.uuid4().hex[:8]}",
            owner_id=owner_id,
            problem_statement="Echo.",
            validator_type=ProblemValidatorType.STANDARD,
        )
    )
    return problem_id


async def _submit(
    session: AsyncSession,
    user_id: str,
    problem_id: str,
    when: datetime,
    verdict: Verdict | None,
) -> str:
    """Insert a submission, its optional judgment, and its solver row."""
    submission_id = str(uuid.uuid4())
    await session.execute(
        arena_submissions.insert().values(
            id=submission_id,
            user_id=user_id,
            problem_id=problem_id,
            language_id="gcc-c17",
            source_code="int main(){}",
            source_hash=submission_id,
            source_size_bytes=12,
            created_at=when,
        )
    )
    if verdict is None:
        return submission_id
    await session.execute(
        arena_submission_judgments.insert().values(
            id=str(uuid.uuid4()),
            submission_id=submission_id,
            status=JudgmentStatus.DONE.value,
            autojudge_verdict=verdict.value,
            final_verdict=verdict.value,
            finished_at=when,
            created_at=when,
        )
    )
    if verdict == Verdict.AC:
        await session.execute(
            arena_problem_solvers.insert().values(
                problem_id=problem_id,
                user_id=user_id,
                solved_at=when,
            )
        )
    return submission_id


async def _add_attempts(
    session: AsyncSession,
    problem_id: str,
    count: int,
    *,
    start: datetime,
    verdict: Verdict | None = Verdict.WA,
) -> None:
    """Add distinct participant attempts to a problem."""
    for offset in range(count):
        await _submit(
            session,
            await _new_user(session),
            problem_id,
            start + timedelta(seconds=offset),
            verdict,
        )


async def _rock_row(session: AsyncSession, user_id: str) -> Row[Any] | None:
    """Return one user's ROCK_CRACKER row, if held."""
    return (
        await session.execute(
            select(arena_user_badges).where(
                arena_user_badges.c.user_id == user_id,
                arena_user_badges.c.badge == ArenaBadge.ROCK_CRACKER.value,
            )
        )
    ).one_or_none()


async def test_live_counts_are_strict_owner_excluding_and_verdict_agnostic(
    session: AsyncSession,
) -> None:
    """The live participant ratio includes unjudged attempts and ignores cached stats."""
    owner = await _new_user(session)
    solver = await _new_user(session)
    problem = await _new_problem(session, owner)
    await _submit(session, solver, problem, _START, Verdict.AC)
    await _add_attempts(session, problem, 4, start=_START + timedelta(minutes=1), verdict=None)

    # Owner activity belongs to neither side of the participant-only ratio.
    await _submit(session, owner, problem, _START + timedelta(minutes=2), Verdict.AC)
    await _submit(session, owner, problem, _START + timedelta(minutes=3), None)
    await session.execute(
        arena_problem_ratings.insert().values(
            problem_id=problem,
            attempted_users=100,
            solved_users=100,
            total_tries_before_solve=100,
        )
    )

    await compute_badge_awards(session, full_reconcile=True)
    assert await _rock_row(session, solver) is None  # 1 / 5 is exactly 20%.
    assert await _rock_row(session, owner) is None

    await _add_attempts(session, problem, 1, start=_START + timedelta(minutes=4), verdict=None)
    await compute_badge_awards(session, full_reconcile=True)

    assert await _rock_row(session, solver) is not None  # 1 / 6 is below 20%.
    assert await _rock_row(session, owner) is None


async def test_non_ac_only_incremental_cycle_awards_current_solvers(session: AsyncSession) -> None:
    """A new non-AC attempt can cross below 20% with no AC event in the batch."""
    owner = await _new_user(session)
    first_solver = await _new_user(session)
    second_solver = await _new_user(session)
    problem = await _new_problem(session, owner)
    await _submit(session, first_solver, problem, _START, Verdict.AC)
    await _submit(session, second_solver, problem, _START + timedelta(seconds=1), Verdict.AC)
    await _add_attempts(session, problem, 8, start=_START + timedelta(minutes=1))
    await compute_badge_awards(session, full_reconcile=True)
    assert await _rock_row(session, first_solver) is None
    assert await _rock_row(session, second_solver) is None

    # Put both ACs outside the overlap window, then add the eleventh participant as WA.
    watermark = _START + timedelta(hours=1)
    await session.execute(update(arena_badge_cycle_state).values(last_processed_at=watermark))
    await _add_attempts(session, problem, 1, start=_START + timedelta(hours=2))

    await compute_badge_awards(session, full_reconcile=False)

    assert await _rock_row(session, first_solver) is not None
    assert await _rock_row(session, second_solver) is not None


async def test_incremental_pass_does_not_revoke_but_full_pass_does(
    session: AsyncSession,
) -> None:
    """Crossing upward revokes only after a complete catalogue view."""
    owner = await _new_user(session)
    holder = await _new_user(session)
    problem = await _new_problem(session, owner)
    await _submit(session, holder, problem, _START, Verdict.AC)
    await _add_attempts(session, problem, 5, start=_START + timedelta(minutes=1))
    await compute_badge_awards(session, full_reconcile=True)
    assert await _rock_row(session, holder) is not None

    await _submit(
        session,
        await _new_user(session),
        problem,
        _START + timedelta(hours=1),
        Verdict.AC,
    )
    await compute_badge_awards(session, full_reconcile=False)
    assert await _rock_row(session, holder) is not None

    await compute_badge_awards(session, full_reconcile=True)
    assert await _rock_row(session, holder) is None


async def test_full_reconcile_with_empty_event_batches_revokes_stale_row(
    session: AsyncSession,
) -> None:
    """Full reconciliation runs even when no submission event exists."""
    user_id = await _new_user(session)
    await session.execute(
        arena_user_badges.insert().values(
            id=str(uuid.uuid4()),
            user_id=user_id,
            badge=ArenaBadge.ROCK_CRACKER.value,
            awarded_at=_START,
        )
    )

    await compute_badge_awards(session, full_reconcile=True)

    assert await _rock_row(session, user_id) is None


async def test_surviving_holder_keeps_original_anchor(session: AsyncSession) -> None:
    """Another qualifying problem preserves the row and its historical anchor."""
    owner = await _new_user(session)
    holder = await _new_user(session)
    first_problem = await _new_problem(session, owner)
    second_problem = await _new_problem(session, owner)
    first_submission = await _submit(session, holder, first_problem, _START, Verdict.AC)
    await _submit(session, holder, second_problem, _START + timedelta(minutes=1), Verdict.AC)
    await _add_attempts(session, first_problem, 5, start=_START + timedelta(minutes=2))
    await _add_attempts(session, second_problem, 5, start=_START + timedelta(minutes=3))
    await compute_badge_awards(session, full_reconcile=True)
    before = await _rock_row(session, holder)
    assert before is not None
    assert before.submission_id == first_submission

    await _submit(
        session,
        await _new_user(session),
        first_problem,
        _START + timedelta(hours=1),
        Verdict.AC,
    )
    await compute_badge_awards(session, full_reconcile=True)

    after = await _rock_row(session, holder)
    assert after is not None
    assert after.id == before.id
    assert after.submission_id == first_submission


async def test_null_anchor_is_filled_without_replacing_badge_row(session: AsyncSession) -> None:
    """An existing unanchored holder participates in the #126 backfill contract."""
    owner = await _new_user(session)
    holder = await _new_user(session)
    problem = await _new_problem(session, owner)
    submission_id = await _submit(session, holder, problem, _START, Verdict.AC)
    await _add_attempts(session, problem, 5, start=_START + timedelta(minutes=1))
    badge_id = str(uuid.uuid4())
    await session.execute(
        arena_user_badges.insert().values(
            id=badge_id,
            user_id=holder,
            badge=ArenaBadge.ROCK_CRACKER.value,
            awarded_at=_START,
            submission_id=None,
        )
    )

    await compute_badge_awards(session, full_reconcile=True)

    row = await _rock_row(session, holder)
    assert row is not None
    assert row.id == badge_id
    assert row.submission_id == submission_id


async def test_revoked_badge_is_reawarded_with_fresh_row_and_anchor(
    session: AsyncSession,
) -> None:
    """Requalification uses ordinary row creation and current provenance."""
    owner = await _new_user(session)
    holder = await _new_user(session)
    first_problem = await _new_problem(session, owner)
    await _submit(session, holder, first_problem, _START, Verdict.AC)
    await _add_attempts(session, first_problem, 5, start=_START + timedelta(minutes=1))
    await compute_badge_awards(session, full_reconcile=True)
    first_row = await _rock_row(session, holder)
    assert first_row is not None

    await _submit(
        session,
        await _new_user(session),
        first_problem,
        _START + timedelta(hours=1),
        Verdict.AC,
    )
    await compute_badge_awards(session, full_reconcile=True)
    assert await _rock_row(session, holder) is None

    second_problem = await _new_problem(session, owner)
    second_submission = await _submit(
        session,
        holder,
        second_problem,
        _START + timedelta(hours=2),
        Verdict.AC,
    )
    await _add_attempts(session, second_problem, 5, start=_START + timedelta(hours=2, minutes=1))
    await compute_badge_awards(session, full_reconcile=True)

    second_row = await _rock_row(session, holder)
    assert second_row is not None
    assert second_row.id != first_row.id
    assert second_row.awarded_at > first_row.awarded_at
    assert second_row.submission_id == second_submission


async def test_withdrawn_ac_revokes_after_solver_reconciliation(session: AsyncSession) -> None:
    """A withdrawn AC changes eligibility through #227's corrected solver rows."""
    owner = await _new_user(session)
    holder = await _new_user(session)
    problem = await _new_problem(session, owner)
    submission_id = await _submit(session, holder, problem, _START, Verdict.AC)
    await _add_attempts(session, problem, 5, start=_START + timedelta(minutes=1))
    await compute_badge_awards(session, full_reconcile=True)
    assert await _rock_row(session, holder) is not None

    await session.execute(
        update(arena_submission_judgments)
        .where(arena_submission_judgments.c.submission_id == submission_id)
        .values(
            autojudge_verdict=Verdict.WA.value,
            final_verdict=Verdict.WA.value,
        )
    )
    await session.execute(
        delete(arena_problem_solvers).where(
            arena_problem_solvers.c.problem_id == problem,
            arena_problem_solvers.c.user_id == holder,
        )
    )

    await compute_badge_awards(session, full_reconcile=True)

    assert await _rock_row(session, holder) is None
