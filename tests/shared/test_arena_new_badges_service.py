#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the newer Arena badge-assignment rules."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import (
    arena_badge_cycle_state,
    arena_problem_ratings,
    arena_problem_set_problems,
    arena_problem_sets,
    arena_problem_solvers,
    arena_submission_judgments,
    arena_submissions,
    arena_user_badges,
    arena_users,
)
from shared.enumerations import ArenaBadge, ArenaRole, JudgmentStatus, Verdict
from shared.services.arena_badges import compute_badge_awards

pytestmark = pytest.mark.asyncio

_WEEKDAY_NOON = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)


async def _new_user(session: AsyncSession, *, role: ArenaRole = ArenaRole.ARENA_USER) -> str:
    """Insert a minimal Arena user and return its id."""
    user_id = str(uuid.uuid4())
    await session.execute(
        arena_users.insert().values(
            id=user_id,
            nome="Badge User",
            email_normalizado=f"{user_id}@test.example",
            password_hash="x",
            role=role,
        )
    )
    return user_id


async def _submit(
    session: AsyncSession,
    user_id: str,
    problem_id: str,
    verdict: Verdict,
    when: datetime,
    *,
    language_id: str = "gcc-c17",
    problem_set_id: str | None = None,
) -> str:
    """Insert a submission plus its single DONE judgment; return submission id."""
    submission_id = str(uuid.uuid4())
    await session.execute(
        arena_submissions.insert().values(
            id=submission_id,
            user_id=user_id,
            problem_id=problem_id,
            language_id=language_id,
            source_code="int main(){}",
            source_hash=submission_id,
            source_size_bytes=12,
            problem_set_id=problem_set_id,
            created_at=when,
        )
    )
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
            arena_problem_solvers.insert()
            .prefix_with("OR IGNORE")
            .values(problem_id=problem_id, user_id=user_id, solved_at=when)
        )
    return submission_id


async def _new_problem_set(
    session: AsyncSession,
    problem_ids: list[str],
    *,
    deadline: datetime | None = None,
) -> str:
    """Insert a minimal problem set containing the provided problems."""
    problem_set_id = str(uuid.uuid4())
    await session.execute(
        arena_problem_sets.insert().values(
            id=problem_set_id,
            class_id=str(uuid.uuid4()),
            name="Badge Set",
            deadline=deadline,
        )
    )
    for problem_id in problem_ids:
        await session.execute(
            arena_problem_set_problems.insert().values(problem_set_id=problem_set_id, problem_id=problem_id)
        )
    return problem_set_id


async def _badges(session: AsyncSession, user_id: str) -> set[ArenaBadge]:
    """Return the set of badges currently held by a user."""
    rows = (
        (await session.execute(select(arena_user_badges.c.badge).where(arena_user_badges.c.user_id == user_id)))
        .scalars()
        .all()
    )
    return {ArenaBadge(b) for b in rows}


async def _watermark(session: AsyncSession) -> datetime | None:
    """Return the persisted incremental watermark."""
    row = (await session.execute(select(arena_badge_cycle_state))).first()
    return row.last_processed_at if row is not None else None


async def test_language_badges_award_thresholds_for_same_problem(session: AsyncSession) -> None:
    """Solving the same problem in 10 languages earns all language-count badges."""
    user = await _new_user(session)
    problem = str(uuid.uuid4())
    for i in range(2):
        await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=i), language_id=f"lang-{i}")
    await compute_badge_awards(session, full_reconcile=True)
    assert ArenaBadge.LANGUAGES_3 not in await _badges(session, user)

    for i in range(2, 10):
        await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=i), language_id=f"lang-{i}")
    await compute_badge_awards(session, full_reconcile=True)

    held = await _badges(session, user)
    assert {ArenaBadge.LANGUAGES_3, ArenaBadge.LANGUAGES_5, ArenaBadge.LANGUAGES_10} <= held


async def test_first_solver_awards_only_earliest_solver(session: AsyncSession) -> None:
    """Only the globally earliest solver of an affected problem earns FIRST_SOLVER."""
    first = await _new_user(session)
    second = await _new_user(session)
    problem = str(uuid.uuid4())
    await _submit(session, first, problem, Verdict.AC, _WEEKDAY_NOON)
    await _submit(session, second, problem, Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=1))

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.FIRST_SOLVER in await _badges(session, first)
    assert ArenaBadge.FIRST_SOLVER not in await _badges(session, second)


async def test_first_solver_skips_admin_and_judge_solvers(session: AsyncSession) -> None:
    """ARENA_ADMIN/ARENA_JUDGE solvers are ineligible; the earliest ARENA_USER solver wins."""
    admin = await _new_user(session, role=ArenaRole.ARENA_ADMIN)
    judge = await _new_user(session, role=ArenaRole.ARENA_JUDGE)
    student = await _new_user(session)
    problem = str(uuid.uuid4())
    await _submit(session, admin, problem, Verdict.AC, _WEEKDAY_NOON)
    await _submit(session, judge, problem, Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=1))
    await _submit(session, student, problem, Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=2))

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.FIRST_SOLVER not in await _badges(session, admin)
    assert ArenaBadge.FIRST_SOLVER not in await _badges(session, judge)
    assert ArenaBadge.FIRST_SOLVER in await _badges(session, student)


async def test_rock_cracker_awards_solvers_below_solve_rate_threshold(session: AsyncSession) -> None:
    """Solvers of a problem with solve rate below 20% earn ROCK_CRACKER."""
    hard_solver = await _new_user(session)
    easy_solver = await _new_user(session)
    hard_problem = str(uuid.uuid4())
    easy_problem = str(uuid.uuid4())
    await _submit(session, hard_solver, hard_problem, Verdict.AC, _WEEKDAY_NOON)
    await _submit(session, easy_solver, easy_problem, Verdict.AC, _WEEKDAY_NOON)
    await session.execute(
        arena_problem_ratings.insert().values(
            problem_id=hard_problem,
            attempted_users=10,
            solved_users=1,
            total_tries_before_solve=1,
        )
    )
    await session.execute(
        arena_problem_ratings.insert().values(
            problem_id=easy_problem,
            attempted_users=10,
            solved_users=2,
            total_tries_before_solve=2,
        )
    )

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.ROCK_CRACKER in await _badges(session, hard_solver)
    assert ArenaBadge.ROCK_CRACKER not in await _badges(session, easy_solver)


async def test_first_to_hand_in_requires_problem_set_opt_in(session: AsyncSession) -> None:
    """The first AC submitted to a problem set earns FIRST_TO_HAND_IN."""
    first = await _new_user(session)
    second = await _new_user(session)
    private_first = await _new_user(session)
    problem = str(uuid.uuid4())
    problem_set = await _new_problem_set(session, [problem])
    await _submit(session, private_first, problem, Verdict.AC, _WEEKDAY_NOON)
    await _submit(session, first, problem, Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=1), problem_set_id=problem_set)
    await _submit(
        session, second, problem, Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=2), problem_set_id=problem_set
    )

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.FIRST_TO_HAND_IN in await _badges(session, first)
    assert ArenaBadge.FIRST_TO_HAND_IN not in await _badges(session, second)
    assert ArenaBadge.FIRST_TO_HAND_IN not in await _badges(session, private_first)


async def test_almost_late_waits_for_deadline_and_awards_last_on_time_ac(session: AsyncSession) -> None:
    """ALMOST_LATE is awarded only after the deadline to the latest on-time AC."""
    early = await _new_user(session)
    late = await _new_user(session)
    too_late = await _new_user(session)
    problem = str(uuid.uuid4())
    deadline = _WEEKDAY_NOON + timedelta(hours=1)
    problem_set = await _new_problem_set(session, [problem], deadline=deadline)
    await _submit(session, early, problem, Verdict.AC, _WEEKDAY_NOON, problem_set_id=problem_set)
    await _submit(session, late, problem, Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=30), problem_set_id=problem_set)
    await _submit(
        session, too_late, problem, Verdict.AC, _WEEKDAY_NOON + timedelta(hours=2), problem_set_id=problem_set
    )

    await compute_badge_awards(session, full_reconcile=True, now=_WEEKDAY_NOON + timedelta(minutes=45))
    assert ArenaBadge.ALMOST_LATE not in await _badges(session, late)

    await compute_badge_awards(session, full_reconcile=True, now=_WEEKDAY_NOON + timedelta(hours=3))

    assert ArenaBadge.ALMOST_LATE in await _badges(session, late)
    assert ArenaBadge.ALMOST_LATE not in await _badges(session, early)
    assert ArenaBadge.ALMOST_LATE not in await _badges(session, too_late)


async def test_this_is_the_way_requires_unbroken_distinct_ac_run(session: AsyncSession) -> None:
    """A non-AC or repeated problem resets the 15-distinct-AC run."""
    qualified = await _new_user(session)
    repeated = await _new_user(session)
    for i in range(14):
        await _submit(session, qualified, str(uuid.uuid4()), Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=i))
    await _submit(session, qualified, str(uuid.uuid4()), Verdict.WA, _WEEKDAY_NOON + timedelta(minutes=14))
    for i in range(15):
        await _submit(session, qualified, str(uuid.uuid4()), Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=15 + i))

    first_problem = str(uuid.uuid4())
    await _submit(session, repeated, first_problem, Verdict.AC, _WEEKDAY_NOON)
    for i in range(13):
        await _submit(session, repeated, str(uuid.uuid4()), Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=i + 1))
    await _submit(session, repeated, first_problem, Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=14))
    for i in range(13):
        await _submit(session, repeated, str(uuid.uuid4()), Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=15 + i))

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.THIS_IS_THE_WAY in await _badges(session, qualified)
    assert ArenaBadge.THIS_IS_THE_WAY not in await _badges(session, repeated)


async def test_lococoder_awards_non_ac_burst_and_advances_watermark(session: AsyncSession) -> None:
    """Three non-AC judgments within 90 seconds earn LOCO_CODER on a non-AC-only cycle."""
    boundary = await _new_user(session)
    outside = await _new_user(session)
    p1 = str(uuid.uuid4())
    p2 = str(uuid.uuid4())
    for offset in (0, 45, 90):
        await _submit(session, boundary, p1, Verdict.WA, _WEEKDAY_NOON + timedelta(seconds=offset))
    for offset in (0, 45, 136):
        await _submit(session, outside, p2, Verdict.WA, _WEEKDAY_NOON + timedelta(seconds=offset))

    await compute_badge_awards(session, full_reconcile=False)

    assert ArenaBadge.LOCO_CODER in await _badges(session, boundary)
    assert ArenaBadge.LOCO_CODER not in await _badges(session, outside)
    assert await _watermark(session) == (_WEEKDAY_NOON + timedelta(seconds=136)).replace(tzinfo=None)
