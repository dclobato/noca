#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for ``arena_user_badges.submission_id``: which submission earned a badge.

Covers the anchor each rule shape chooses (the qualifying AC for an event badge,
the crossing submission for an aggregate one) and the desired-state
reconciliation built on it: a full pass revokes what is no longer earned,
re-anchors a surviving holder whose canonical submission moved, and writes
nothing at all over an unchanged database, while an incremental pass never
revokes.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import (
    arena_problem_solvers,
    arena_submission_judgments,
    arena_submissions,
    arena_user_badges,
)
from shared.enumerations import ArenaBadge, Verdict
from shared.services.arena_badge_rules import streak_crossing_days
from shared.services.arena_badges import compute_badge_awards
from tests.shared.test_arena_new_badges_service import (
    _WEEKDAY_NOON,
    _new_problem,
    _new_problem_set,
    _new_user,
    _submit,
)

pytestmark = pytest.mark.asyncio


async def _anchor(session: AsyncSession, user_id: str, badge: ArenaBadge) -> str | None:
    """Return the submission a user's badge is anchored to, or None."""
    return (
        await session.execute(
            select(arena_user_badges.c.submission_id).where(
                arena_user_badges.c.user_id == user_id,
                arena_user_badges.c.badge == badge.value,
            )
        )
    ).scalar_one()


async def _ledger(session: AsyncSession) -> set[tuple[str, str]]:
    """Return every ``(user_id, badge)`` in the ledger."""
    rows = (await session.execute(select(arena_user_badges.c.user_id, arena_user_badges.c.badge))).all()
    return {(user_id, badge) for user_id, badge in rows}


async def _rows(session: AsyncSession) -> dict[tuple[str, str], tuple[str, datetime]]:
    """Return every ledger row as ``(user, badge) -> (anchor, awarded_at)``."""
    rows = (
        await session.execute(
            select(
                arena_user_badges.c.user_id,
                arena_user_badges.c.badge,
                arena_user_badges.c.submission_id,
                arena_user_badges.c.awarded_at,
            )
        )
    ).all()
    return {(row.user_id, row.badge): (row.submission_id, row.awarded_at) for row in rows}


async def _rejudge(session: AsyncSession, submission_id: str, verdict: Verdict) -> None:
    """Flip a submission's active judgment to ``verdict``, as a rejudge would.

    The solver row goes with it when the submission stops being Accepted; that is
    what the Arena does, and the badge rules read both.
    """
    await session.execute(
        update(arena_submission_judgments)
        .where(arena_submission_judgments.c.submission_id == submission_id)
        .values(autojudge_verdict=verdict.value, final_verdict=verdict.value)
    )
    if verdict is Verdict.AC:
        return
    problem_id, user_id = (
        await session.execute(
            select(arena_submissions.c.problem_id, arena_submissions.c.user_id).where(
                arena_submissions.c.id == submission_id
            )
        )
    ).one()
    remaining = (
        await session.execute(
            select(arena_submissions.c.id)
            .join(
                arena_submission_judgments,
                arena_submission_judgments.c.submission_id == arena_submissions.c.id,
            )
            .where(
                arena_submissions.c.user_id == user_id,
                arena_submissions.c.problem_id == problem_id,
                arena_submission_judgments.c.final_verdict == Verdict.AC.value,
            )
        )
    ).first()
    if remaining is None:
        await session.execute(
            delete(arena_problem_solvers).where(
                arena_problem_solvers.c.user_id == user_id,
                arena_problem_solvers.c.problem_id == problem_id,
            )
        )


async def test_streak_crossing_days_reports_first_day_each_run_length_is_reached() -> None:
    """A threshold is anchored to the day a run first reaches it, not the latest solve."""
    days = [
        datetime(2026, 6, 1).date(),
        datetime(2026, 6, 2).date(),
        datetime(2026, 6, 3).date(),  # run of 3 completes here
        datetime(2026, 6, 10).date(),  # gap resets the run
        datetime(2026, 6, 11).date(),
    ]
    crossings = streak_crossing_days(days, (3, 7))
    assert crossings[3] == datetime(2026, 6, 3).date()
    assert 7 not in crossings


async def test_event_badge_is_anchored_to_the_qualifying_submission(session: AsyncSession) -> None:
    """ONE_SHOT points at the first-attempt AC that earned it."""
    user = await _new_user(session)
    problem = await _new_problem(session, await _new_user(session))
    submission = await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON)

    await compute_badge_awards(session, full_reconcile=True)

    assert await _anchor(session, user, ArenaBadge.ONE_SHOT) == submission
    assert await _anchor(session, user, ArenaBadge.HELLO_WORLD) == submission


async def test_event_badge_anchors_to_the_earliest_qualifying_submission(session: AsyncSession) -> None:
    """Later qualifying ACs never move an anchor already set by an earlier one."""
    user = await _new_user(session)
    owner = await _new_user(session)
    first = await _submit(session, user, await _new_problem(session, owner), Verdict.AC, _WEEKDAY_NOON)
    await _submit(session, user, await _new_problem(session, owner), Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=5))

    await compute_badge_awards(session, full_reconcile=True)
    await compute_badge_awards(session, full_reconcile=True)

    assert await _anchor(session, user, ArenaBadge.HELLO_WORLD) == first


async def test_streak_badge_is_anchored_to_the_submission_that_completed_the_run(
    session: AsyncSession,
) -> None:
    """STRIKE_3 points at the AC on the third consecutive day, not the latest one."""
    user = await _new_user(session)
    owner = await _new_user(session)
    submissions = [
        await _submit(session, user, await _new_problem(session, owner), Verdict.AC, _WEEKDAY_NOON + timedelta(days=d))
        for d in range(5)
    ]

    await compute_badge_awards(session, full_reconcile=True)

    assert await _anchor(session, user, ArenaBadge.STRIKE_3) == submissions[2]


async def test_problem_count_badge_is_anchored_to_the_tenth_distinct_solve(session: AsyncSession) -> None:
    """PROBLEMS_10 points at the AC on the tenth distinct problem the user solved."""
    user = await _new_user(session)
    owner = await _new_user(session)
    submissions = [
        await _submit(
            session, user, await _new_problem(session, owner), Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=i)
        )
        for i in range(12)
    ]

    await compute_badge_awards(session, full_reconcile=True)

    assert await _anchor(session, user, ArenaBadge.PROBLEMS_10) == submissions[9]


async def test_language_badge_is_anchored_to_the_threshold_crossing_submission(session: AsyncSession) -> None:
    """LANGUAGES_3 points at the AC that brought the pair to three distinct languages."""
    user = await _new_user(session)
    problem = await _new_problem(session, await _new_user(session))
    submissions = [
        await _submit(
            session,
            user,
            problem,
            Verdict.AC,
            _WEEKDAY_NOON + timedelta(minutes=i),
            language_id=f"lang-{i}",
        )
        for i in range(5)
    ]

    await compute_badge_awards(session, full_reconcile=True)

    assert await _anchor(session, user, ArenaBadge.LANGUAGES_3) == submissions[2]


async def test_first_to_hand_in_is_anchored_to_the_winning_submission(session: AsyncSession) -> None:
    """FIRST_TO_HAND_IN points at the submission that actually won the pair."""
    owner = await _new_user(session)
    winner = await _new_user(session)
    runner_up = await _new_user(session)
    problem = await _new_problem(session, owner)
    problem_set = await _new_problem_set(session, [problem])
    winning = await _submit(session, winner, problem, Verdict.AC, _WEEKDAY_NOON, problem_set_id=problem_set)
    await _submit(
        session,
        runner_up,
        problem,
        Verdict.AC,
        _WEEKDAY_NOON + timedelta(minutes=1),
        problem_set_id=problem_set,
    )

    await compute_badge_awards(session, full_reconcile=True)

    assert await _anchor(session, winner, ArenaBadge.FIRST_TO_HAND_IN) == winning


async def test_a_reconcile_over_an_unchanged_database_writes_nothing(session: AsyncSession) -> None:
    """The desired-state diff must be a no-op when nothing moved.

    Not merely "awards nothing": no row may be inserted, re-anchored or deleted,
    because every full pass now rewrites this table and an unconditional write
    would leave a dead tuple per badge per cycle.
    """
    user = await _new_user(session)
    owner = await _new_user(session)
    for day in range(3):
        await _submit(
            session, user, await _new_problem(session, owner), Verdict.AC, _WEEKDAY_NOON + timedelta(days=day)
        )
    await compute_badge_awards(session, full_reconcile=True)
    before = await _rows(session)
    assert before

    assert await compute_badge_awards(session, full_reconcile=True) == 0

    assert await _rows(session) == before


async def test_full_reconcile_revokes_a_badge_whose_criterion_no_longer_holds(
    session: AsyncSession,
) -> None:
    """A rejudge off Accepted takes the badges that rested on it.

    This is the production Night Worker case in miniature: the holder keeps a
    row today because nothing re-examines it. Under desired-state reconciliation
    the next full pass simply does not derive it.
    """
    user = await _new_user(session)
    problem = await _new_problem(session, await _new_user(session))
    submission = await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON)
    await compute_badge_awards(session, full_reconcile=True)
    assert ArenaBadge.ONE_SHOT.value in {badge for _, badge in await _ledger(session)}

    await _rejudge(session, submission, Verdict.WA)
    await compute_badge_awards(session, full_reconcile=True)

    assert await _ledger(session) == set()


async def _earn_many_families(session: AsyncSession) -> tuple[str, set[str]]:
    """Build a corpus that earns badges from every rule family; return the holder.

    Deliberately broad rather than deep: the point of the reconciliation is that
    no family is append-only any more, so a revocation test has to touch all of
    them at once.
    """
    owner = await _new_user(session)
    user = await _new_user(session)
    night = _WEEKDAY_NOON.replace(hour=2)
    # Per-AC recovery badges plus the language tiers, all on one problem.
    recovery_problem = await _new_problem(session, owner)
    for index, verdict in enumerate((Verdict.WA, Verdict.TLE, Verdict.RE)):
        await _submit(session, user, recovery_problem, verdict, night + timedelta(minutes=index))
    for index in range(3):
        await _submit(
            session,
            user,
            recovery_problem,
            Verdict.AC,
            night + timedelta(minutes=10 + index),
            language_id=f"lang-{index}",
        )
    # Distinct problems over consecutive days: streaks, problem counts, and the
    # unbroken-run badge; the first solve of each also makes the user FIRST_SOLVER.
    for day in range(16):
        await _submit(
            session,
            user,
            await _new_problem(session, owner),
            Verdict.AC,
            night + timedelta(days=day, hours=1),
        )
    # A problem set with a passed deadline: FULL_CLEAR, FIRST_TO_HAND_IN, ALMOST_LATE.
    set_problem = await _new_problem(session, owner)
    problem_set = await _new_problem_set(session, [set_problem], deadline=night + timedelta(days=30))
    await _submit(session, user, set_problem, Verdict.AC, night + timedelta(days=20), problem_set_id=problem_set)
    # A burst of three non-AC verdicts inside the window: LOCO_CODER.
    burst_problem = await _new_problem(session, owner)
    for index in range(3):
        await _submit(session, user, burst_problem, Verdict.WA, night + timedelta(days=40, seconds=20 * index))

    await compute_badge_awards(session, full_reconcile=True, now=night + timedelta(days=60))
    held = {badge for holder, badge in await _ledger(session) if holder == user}
    return user, held


async def test_every_family_is_revoked_when_the_corpus_goes_away(session: AsyncSession) -> None:
    """No family is append-only any more, and an empty batch is a real answer.

    A full pass with nothing to look at must still invoke every rule: the empty
    desired state is exactly what revokes the last remaining rows.
    """
    user, held = await _earn_many_families(session)
    assert {
        ArenaBadge.HELLO_WORLD.value,
        ArenaBadge.NIGHT_WORKER.value,
        ArenaBadge.ONE_SHOT.value,
        ArenaBadge.BUG_KILLER.value,
        ArenaBadge.BIT_SCRUBBER.value,
        ArenaBadge.LANGUAGES_3.value,
        ArenaBadge.STRIKE_3.value,
        ArenaBadge.STRIKE_7.value,
        ArenaBadge.PROBLEMS_10.value,
        ArenaBadge.FIRST_SOLVER.value,
        ArenaBadge.THIS_IS_THE_WAY.value,
        ArenaBadge.FULL_CLEAR.value,
        ArenaBadge.FIRST_TO_HAND_IN.value,
        ArenaBadge.ALMOST_LATE.value,
        ArenaBadge.LOCO_CODER.value,
    } <= held

    await session.execute(delete(arena_problem_solvers))
    await session.execute(delete(arena_submission_judgments))
    await session.execute(delete(arena_submissions))
    await compute_badge_awards(session, full_reconcile=True)

    assert {holder for holder, _ in await _ledger(session)} == set()
    assert user not in {holder for holder, _ in await _ledger(session)}


async def test_an_incremental_pass_never_revokes(session: AsyncSession) -> None:
    """An incremental pass sees a subset of history and must not act on the gap."""
    user = await _new_user(session)
    problem = await _new_problem(session, await _new_user(session))
    submission = await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON)
    await compute_badge_awards(session, full_reconcile=True)
    before = await _ledger(session)

    await _rejudge(session, submission, Verdict.WA)
    await compute_badge_awards(session, full_reconcile=False)

    assert await _ledger(session) == before


async def test_a_surviving_holder_is_reanchored_when_its_submission_stops_qualifying(
    session: AsyncSession,
) -> None:
    """Eligibility can outlive the anchor; the row must follow the live witness.

    The user solves two problems at night. The earlier AC anchors NIGHT_WORKER;
    rejudging it away leaves the badge earned by the later one, so the row keeps
    its ``awarded_at`` and moves to the submission that still earns it.
    """
    user = await _new_user(session)
    owner = await _new_user(session)
    midnight = _WEEKDAY_NOON.replace(hour=1)
    first = await _submit(session, user, await _new_problem(session, owner), Verdict.AC, midnight)
    second = await _submit(
        session, user, await _new_problem(session, owner), Verdict.AC, midnight + timedelta(minutes=30)
    )
    await compute_badge_awards(session, full_reconcile=True)
    assert await _anchor(session, user, ArenaBadge.NIGHT_WORKER) == first
    awarded_at = (await _rows(session))[(user, ArenaBadge.NIGHT_WORKER.value)][1]

    await _rejudge(session, first, Verdict.WA)
    await compute_badge_awards(session, full_reconcile=True)

    assert await _anchor(session, user, ArenaBadge.NIGHT_WORKER) == second
    assert (await _rows(session))[(user, ArenaBadge.NIGHT_WORKER.value)][1] == awarded_at


async def test_repeated_reconciles_never_move_a_still_canonical_anchor(session: AsyncSession) -> None:
    """A later qualifying AC does not displace the earliest one.

    Re-anchoring follows the canonical rule, so it fires only when the canonical
    submission actually changes -- never merely because more work arrived.
    """
    user = await _new_user(session)
    problem = await _new_problem(session, await _new_user(session))
    await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON)
    await compute_badge_awards(session, full_reconcile=True)
    anchor_before = await _anchor(session, user, ArenaBadge.HELLO_WORLD)
    await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=1))

    await compute_badge_awards(session, full_reconcile=True)

    assert await _anchor(session, user, ArenaBadge.HELLO_WORLD) == anchor_before


async def test_full_clear_is_anchored_to_the_submission_that_completed_the_set(
    session: AsyncSession,
) -> None:
    """FULL_CLEAR points at the AC on the set's last-solved problem, not the batch trigger."""
    owner = await _new_user(session)
    user = await _new_user(session)
    first_problem = await _new_problem(session, owner)
    last_problem = await _new_problem(session, owner)
    await _new_problem_set(session, [first_problem, last_problem])
    await _submit(session, user, first_problem, Verdict.AC, _WEEKDAY_NOON)
    completing = await _submit(session, user, last_problem, Verdict.AC, _WEEKDAY_NOON + timedelta(hours=1))

    await compute_badge_awards(session, full_reconcile=True)

    assert await _anchor(session, user, ArenaBadge.FULL_CLEAR) == completing


async def test_full_clear_prefers_the_set_completed_first_even_when_sets_are_disjoint(
    session: AsyncSession,
) -> None:
    """The earliest-completed set wins even when it shares no problem with the first event.

    Set A is solved at T1 and T4, set B at T2 and T3, so B is completed first.
    Choosing from the T1 event's problem alone would only ever see set A.
    """
    owner = await _new_user(session)
    user = await _new_user(session)
    a_first, a_last, b_first, b_last = [await _new_problem(session, owner) for _ in range(4)]
    await _new_problem_set(session, [a_first, a_last])
    await _new_problem_set(session, [b_first, b_last])
    await _submit(session, user, a_first, Verdict.AC, _WEEKDAY_NOON)
    await _submit(session, user, b_first, Verdict.AC, _WEEKDAY_NOON + timedelta(hours=1))
    completing = await _submit(session, user, b_last, Verdict.AC, _WEEKDAY_NOON + timedelta(hours=2))
    await _submit(session, user, a_last, Verdict.AC, _WEEKDAY_NOON + timedelta(hours=3))

    await compute_badge_awards(session, full_reconcile=True)

    assert await _anchor(session, user, ArenaBadge.FULL_CLEAR) == completing
