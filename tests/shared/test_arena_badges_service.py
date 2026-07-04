#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the rating worker's badge-assignment service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import (
    arena_badge_cycle_state,
    arena_problem_set_problems,
    arena_problem_solvers,
    arena_submission_judgments,
    arena_submissions,
    arena_user_badges,
    arena_users,
)
from shared.enumerations import ArenaBadge, ArenaRole, JudgmentStatus, Verdict
from shared.services.arena_badges import compute_badge_awards

pytestmark = pytest.mark.asyncio


async def _new_user(
    session: AsyncSession,
    *,
    country: str | None = None,
    subdivision: str | None = None,
    role: ArenaRole = ArenaRole.ARENA_USER,
) -> str:
    """Insert a minimal Arena user and return its id."""
    user_id = str(uuid.uuid4())
    await session.execute(
        arena_users.insert().values(
            id=user_id,
            nome="Badge User",
            email_normalizado=f"{user_id}@test.example",
            password_hash="x",
            role=role,
            country_code=country,
            subdivision_code=subdivision,
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
    wall_ms: int | None = None,
    memory_kb: int | None = None,
    submission_id: str | None = None,
) -> str:
    """Insert a submission plus its single DONE judgment; return submission id."""
    submission_id = submission_id or str(uuid.uuid4())
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
    await session.execute(
        arena_submission_judgments.insert().values(
            id=str(uuid.uuid4()),
            submission_id=submission_id,
            status=JudgmentStatus.DONE.value,
            autojudge_verdict=verdict.value,
            final_verdict=verdict.value,
            max_wall_time_ms=wall_ms,
            max_memory_kb=memory_kb,
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


async def _badges(session: AsyncSession, user_id: str) -> set[ArenaBadge]:
    """Return the set of badges currently held by a user."""
    rows = (
        (await session.execute(select(arena_user_badges.c.badge).where(arena_user_badges.c.user_id == user_id)))
        .scalars()
        .all()
    )
    return {ArenaBadge(b) for b in rows}


# Anchor times: a known Saturday and a weekday daytime, both in UTC.
_SATURDAY = datetime(2026, 6, 20, 15, 0, tzinfo=UTC)  # Sat afternoon UTC
_WEEKDAY_NOON = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)  # Mon noon UTC
_NIGHT_UTC = datetime(2026, 6, 22, 2, 0, tzinfo=UTC)  # 02:00 UTC -> night in UTC


async def test_hello_world_and_one_shot(session: AsyncSession) -> None:
    """A single Accepted submission earns HELLO_WORLD and ONE_SHOT."""
    user = await _new_user(session)
    problem = str(uuid.uuid4())
    await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON)

    awarded = await compute_badge_awards(session, full_reconcile=True)

    assert awarded >= 2
    assert {ArenaBadge.HELLO_WORLD, ArenaBadge.ONE_SHOT} <= await _badges(session, user)


async def test_night_and_weekend_worker(session: AsyncSession) -> None:
    """Night and weekend AC timestamps earn the time-of-day badges (UTC user)."""
    night_user = await _new_user(session)
    weekend_user = await _new_user(session)
    await _submit(session, night_user, str(uuid.uuid4()), Verdict.AC, _NIGHT_UTC)
    await _submit(session, weekend_user, str(uuid.uuid4()), Verdict.AC, _SATURDAY)

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.NIGHT_WORKER in await _badges(session, night_user)
    assert ArenaBadge.NIGHT_WORKER not in await _badges(session, weekend_user)
    assert ArenaBadge.WEEKEND_WORKER in await _badges(session, weekend_user)


async def test_night_worker_uses_user_timezone(session: AsyncSession) -> None:
    """02:00 UTC is daytime in Brazil (UTC-3), so no NIGHT_WORKER for a BR user."""
    br_user = await _new_user(session, country="BR", subdivision="BR-SP")
    # 02:00 UTC -> 23:00 previous day in Sao Paulo: still not night [0,5).
    await _submit(session, br_user, str(uuid.uuid4()), Verdict.AC, _NIGHT_UTC)
    # 05:00 UTC -> 02:00 Sao Paulo: night.
    br_night = await _new_user(session, country="BR", subdivision="BR-SP")
    await _submit(session, br_night, str(uuid.uuid4()), Verdict.AC, _NIGHT_UTC.replace(hour=5))

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.NIGHT_WORKER not in await _badges(session, br_user)
    assert ArenaBadge.NIGHT_WORKER in await _badges(session, br_night)


async def test_never_give_up_after_five_wa(session: AsyncSession) -> None:
    """Five WA before the AC earns NEVER_GIVE_UP (and not ONE_SHOT)."""
    user = await _new_user(session)
    problem = str(uuid.uuid4())
    base = _WEEKDAY_NOON
    for i in range(5):
        await _submit(session, user, problem, Verdict.WA, base + timedelta(minutes=i))
    await _submit(session, user, problem, Verdict.AC, base + timedelta(minutes=10))

    await compute_badge_awards(session, full_reconcile=True)

    held = await _badges(session, user)
    assert ArenaBadge.NEVER_GIVE_UP in held
    assert ArenaBadge.ONE_SHOT not in held


async def test_bit_scrubber_after_tle(session: AsyncSession) -> None:
    """A prior TLE before the AC earns BIT_SCRUBBER."""
    user = await _new_user(session)
    problem = str(uuid.uuid4())
    await _submit(session, user, problem, Verdict.TLE, _WEEKDAY_NOON)
    await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=1))

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.BIT_SCRUBBER in await _badges(session, user)


async def test_bug_killer_on_later_ac_after_re(session: AsyncSession) -> None:
    """AC -> RE -> AC awards BUG_KILLER on the third event (later-qualifying AC)."""
    user = await _new_user(session)
    problem = str(uuid.uuid4())
    base = _WEEKDAY_NOON
    await _submit(session, user, problem, Verdict.AC, base)
    await _submit(session, user, problem, Verdict.RE, base + timedelta(minutes=1))
    await _submit(session, user, problem, Verdict.AC, base + timedelta(minutes=2))

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.BUG_KILLER in await _badges(session, user)


async def test_trimmer_after_presentation_error(session: AsyncSession) -> None:
    """A PE immediately followed by AC earns TRIMMER."""
    user = await _new_user(session)
    problem = str(uuid.uuid4())
    await _submit(session, user, problem, Verdict.PE, _WEEKDAY_NOON)
    await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=1))

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.TRIMMER in await _badges(session, user)


async def test_admin_users_are_not_excluded(session: AsyncSession) -> None:
    """Badge eligibility does not exclude admins/authors."""
    admin = await _new_user(session, role=ArenaRole.ARENA_ADMIN)
    await _submit(session, admin, str(uuid.uuid4()), Verdict.AC, _WEEKDAY_NOON)

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.HELLO_WORLD in await _badges(session, admin)


async def test_full_clear_when_all_set_problems_solved(session: AsyncSession) -> None:
    """Solving every problem of a set earns FULL_CLEAR; a problem may live in many sets."""
    user = await _new_user(session)
    set_a, set_b = str(uuid.uuid4()), str(uuid.uuid4())
    p1, p2, p3 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    # set_a = {p1, p2}; set_b = {p1, p3}. p1 belongs to both.
    for ps, problem in [(set_a, p1), (set_a, p2), (set_b, p1), (set_b, p3)]:
        await session.execute(arena_problem_set_problems.insert().values(problem_set_id=ps, problem_id=problem))
    base = _WEEKDAY_NOON
    await _submit(session, user, p1, Verdict.AC, base)
    await _submit(session, user, p2, Verdict.AC, base + timedelta(minutes=1))

    await compute_badge_awards(session, full_reconcile=True)

    # set_a fully solved -> FULL_CLEAR; set_b still missing p3 but that is fine.
    assert ArenaBadge.FULL_CLEAR in await _badges(session, user)


async def test_clean_code_by_memory_only(session: AsyncSession) -> None:
    """A slow but lean solution still earns CLEAN_CODE via the memory percentile."""
    problem = str(uuid.uuid4())
    fast_lean = await _new_user(session)
    others = [await _new_user(session) for _ in range(19)]
    base = _WEEKDAY_NOON
    # The target is the slowest by time but the lowest by memory.
    await _submit(session, fast_lean, problem, Verdict.AC, base, wall_ms=10_000, memory_kb=100)
    for i, u in enumerate(others):
        await _submit(session, u, problem, Verdict.AC, base + timedelta(minutes=i + 1), wall_ms=100, memory_kb=5_000)

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.CLEAN_CODE in await _badges(session, fast_lean)


async def test_clean_code_dynamic_awards_older_ac_on_reconcile(session: AsyncSession) -> None:
    """An older AC that did NOT qualify enters the top 5% as the population grows.

    Memory is left null so only the wall-time percentile decides eligibility.
    """
    problem = str(uuid.uuid4())
    target = await _new_user(session)
    faster = await _new_user(session)
    base = _WEEKDAY_NOON
    # Two ACs: with n=2 the top-5% threshold is the single fastest (10), so the
    # target (100) does NOT qualify yet.
    await _submit(session, target, problem, Verdict.AC, base, wall_ms=100, memory_kb=None)
    await _submit(session, faster, problem, Verdict.AC, base + timedelta(minutes=1), wall_ms=10, memory_kb=None)
    await compute_badge_awards(session, full_reconcile=True)
    assert ArenaBadge.CLEAN_CODE not in await _badges(session, target)

    # Add 19 slower solutions. Now n=21, the threshold index rises to the 2nd
    # fastest (100), so the target's older AC enters the top 5%.
    for i in range(19):
        u = await _new_user(session)
        await _submit(session, u, problem, Verdict.AC, base + timedelta(minutes=i + 2), wall_ms=1000, memory_kb=None)
    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.CLEAN_CODE in await _badges(session, target)


async def test_strike_badges_and_historical_max(session: AsyncSession) -> None:
    """A past 3-day run earns STRIKE_3 even after a gap and a single recent AC."""
    user = await _new_user(session)
    day0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    for offset in (0, 1, 2):  # three consecutive days
        await _submit(session, user, str(uuid.uuid4()), Verdict.AC, day0 + timedelta(days=offset))
    # Big gap, then one recent AC: current streak resets to 1 but history keeps the run.
    await _submit(session, user, str(uuid.uuid4()), Verdict.AC, day0 + timedelta(days=40))

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.STRIKE_3 in await _badges(session, user)
    row = (await session.execute(select(arena_users).where(arena_users.c.id == user))).one()
    assert row.current_streak == 1
    assert row.longest_streak >= 3
    assert row.last_ac_date == (day0 + timedelta(days=40)).date()


async def test_idempotent_rerun(session: AsyncSession) -> None:
    """Re-running awards nothing new and leaves streak state unchanged."""
    user = await _new_user(session)
    problem = str(uuid.uuid4())
    await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON)

    first = await compute_badge_awards(session, full_reconcile=True)
    before = await _badges(session, user)
    second = await compute_badge_awards(session, full_reconcile=True)

    assert first > 0
    assert second == 0
    assert await _badges(session, user) == before


async def test_incremental_watermark_processes_equal_finished_at(session: AsyncSession) -> None:
    """Two ACs sharing the max finished_at are both processed across cycles."""
    u1 = await _new_user(session)
    u2 = await _new_user(session)
    # First incremental cycle sees only u1's AC and advances the watermark.
    await _submit(session, u1, str(uuid.uuid4()), Verdict.AC, _WEEKDAY_NOON)
    await compute_badge_awards(session, full_reconcile=False, lookback_seconds=600)
    assert ArenaBadge.HELLO_WORLD in await _badges(session, u1)

    # A later AC shares the same finished_at instant; lookback re-sees it.
    await _submit(session, u2, str(uuid.uuid4()), Verdict.AC, _WEEKDAY_NOON)
    await compute_badge_awards(session, full_reconcile=False, lookback_seconds=600)
    assert ArenaBadge.HELLO_WORLD in await _badges(session, u2)


async def test_ordering_breaks_ties_by_id(session: AsyncSession) -> None:
    """Equal created_at: the AC ordered after the RE by id still earns BUG_KILLER."""
    user = await _new_user(session)
    problem = str(uuid.uuid4())
    # Same timestamp; ids chosen so the RE sorts before the AC.
    re_id, ac_id = "aaaa", "bbbb"
    await _submit(session, user, problem, Verdict.RE, _WEEKDAY_NOON, submission_id=re_id)
    await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON, submission_id=ac_id)

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.BUG_KILLER in await _badges(session, user)


async def test_problem_count_badges_award_crossed_thresholds(session: AsyncSession) -> None:
    """Solving 10 distinct problems earns PROBLEMS_10 (but not the higher tiers)."""
    user = await _new_user(session)
    base = _WEEKDAY_NOON
    for i in range(10):
        await _submit(session, user, str(uuid.uuid4()), Verdict.AC, base + timedelta(minutes=i))

    await compute_badge_awards(session, full_reconcile=True)

    held = await _badges(session, user)
    assert ArenaBadge.PROBLEMS_10 in held
    assert ArenaBadge.PROBLEMS_25 not in held


async def test_problem_count_badge_not_awarded_below_threshold(session: AsyncSession) -> None:
    """Nine distinct solved problems do not earn the 10-problems badge."""
    user = await _new_user(session)
    base = _WEEKDAY_NOON
    for i in range(9):
        await _submit(session, user, str(uuid.uuid4()), Verdict.AC, base + timedelta(minutes=i))

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.PROBLEMS_10 not in await _badges(session, user)


async def test_problem_count_distinct_only(session: AsyncSession) -> None:
    """Repeated AC submissions on the same problem count once toward the threshold."""
    user = await _new_user(session)
    problem = str(uuid.uuid4())
    base = _WEEKDAY_NOON
    for i in range(10):
        await _submit(session, user, problem, Verdict.AC, base + timedelta(minutes=i))

    await compute_badge_awards(session, full_reconcile=True)

    assert ArenaBadge.PROBLEMS_10 not in await _badges(session, user)


async def _watermark(session: AsyncSession) -> datetime | None:
    """Return the persisted incremental watermark."""
    row = (await session.execute(select(arena_badge_cycle_state))).first()
    return row.last_processed_at if row is not None else None


async def test_reconcile_not_forced_when_recently_reconciled(session: AsyncSession) -> None:
    """With full_reconcile=None and a recent reconcile, a restart stays incremental.

    A previously-judged AC whose finished_at predates the watermark window must NOT
    be picked up by the incremental pass once reconciliation has already run.
    """
    user = await _new_user(session)
    # First automatic cycle: no prior reconcile -> full reconcile, sets the cursor.
    await _submit(session, user, str(uuid.uuid4()), Verdict.AC, _WEEKDAY_NOON)
    await compute_badge_awards(session, now=_WEEKDAY_NOON)

    # An old AC (well before the watermark - lookback) appears; simulate a restart by
    # calling again with the mode left to be derived from persisted state.
    old_user = await _new_user(session)
    await _submit(session, old_user, str(uuid.uuid4()), Verdict.AC, _WEEKDAY_NOON - timedelta(days=1))
    awarded = await compute_badge_awards(
        session,
        reconcile_interval_seconds=86400,
        lookback_seconds=600,
        now=_WEEKDAY_NOON + timedelta(minutes=5),
    )

    # Recent reconcile => incremental only => the old AC is not seen.
    assert awarded == 0
    assert ArenaBadge.HELLO_WORLD not in await _badges(session, old_user)


async def test_watermark_does_not_regress(session: AsyncSession) -> None:
    """An empty incremental cycle never moves the watermark backward."""
    user = await _new_user(session)
    await _submit(session, user, str(uuid.uuid4()), Verdict.AC, _WEEKDAY_NOON)
    await compute_badge_awards(session, full_reconcile=True, now=_WEEKDAY_NOON)
    high = await _watermark(session)
    assert high is not None

    # A later incremental cycle with no new work must keep the watermark.
    await compute_badge_awards(session, full_reconcile=False, now=_WEEKDAY_NOON + timedelta(hours=1))
    assert await _watermark(session) == high
