#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for ``arena_user_badges.submission_id``: which submission earned a badge.

Covers the anchor each rule shape chooses (the qualifying AC for an event badge,
the crossing submission for an aggregate one), the never-rewrite rule in
``award_badge``, and the self-backfill: a full reconcile must anchor rows that
predate the column without awarding or revoking a single badge.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import arena_badge_cycle_state, arena_user_badges
from shared.enumerations import ArenaBadge, Verdict
from shared.services.arena_badge_data import award_badge
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


async def _clear_anchors(session: AsyncSession) -> None:
    """Null every anchor, reproducing the state left by the migration."""
    await session.execute(update(arena_user_badges).values(submission_id=None))


async def _ledger(session: AsyncSession) -> set[tuple[str, str]]:
    """Return every ``(user_id, badge)`` in the ledger."""
    rows = (await session.execute(select(arena_user_badges.c.user_id, arena_user_badges.c.badge))).all()
    return {(user_id, badge) for user_id, badge in rows}


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


async def test_award_badge_fills_a_null_anchor_but_never_rewrites_one(session: AsyncSession) -> None:
    """The first anchor wins; a NULL one is filled in by the next pass."""
    user = await _new_user(session)
    problem = await _new_problem(session, await _new_user(session))
    first = await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON)
    second = await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=1))

    assert await award_badge(session, user, ArenaBadge.HELLO_WORLD, first) is True
    assert await award_badge(session, user, ArenaBadge.HELLO_WORLD, second) is False
    assert await _anchor(session, user, ArenaBadge.HELLO_WORLD) == first

    await session.execute(update(arena_user_badges).values(submission_id=None))
    assert await award_badge(session, user, ArenaBadge.HELLO_WORLD, second) is False
    assert await _anchor(session, user, ArenaBadge.HELLO_WORLD) == second


async def test_clean_code_style_award_without_a_submission_leaves_the_anchor_null(
    session: AsyncSession,
) -> None:
    """A rule that passes no submission stores NULL, as CLEAN_CODE does."""
    user = await _new_user(session)

    assert await award_badge(session, user, ArenaBadge.CLEAN_CODE) is True

    assert await _anchor(session, user, ArenaBadge.CLEAN_CODE) is None


async def test_full_reconcile_anchors_pre_existing_rows_without_changing_the_ledger(
    session: AsyncSession,
) -> None:
    """Rows predating the column are anchored by an ordinary reconcile, awarding nothing.

    This is the whole backfill story: no data migration and no one-off script,
    because ``award_badge`` fills a NULL anchor and the ``owned`` short-circuits
    skip only badges that already carry one.
    """
    user = await _new_user(session)
    owner = await _new_user(session)
    submissions = [
        await _submit(session, user, await _new_problem(session, owner), Verdict.AC, _WEEKDAY_NOON + timedelta(days=d))
        for d in range(3)
    ]
    await compute_badge_awards(session, full_reconcile=True)
    ledger_before = await _ledger(session)
    await _clear_anchors(session)

    assert await compute_badge_awards(session, full_reconcile=True) == 0

    assert await _ledger(session) == ledger_before
    assert await _anchor(session, user, ArenaBadge.HELLO_WORLD) == submissions[0]
    assert await _anchor(session, user, ArenaBadge.STRIKE_3) == submissions[2]


async def test_repeated_reconciles_never_move_an_existing_anchor(session: AsyncSession) -> None:
    """Once anchored, a badge keeps the submission the live award recorded."""
    user = await _new_user(session)
    problem = await _new_problem(session, await _new_user(session))
    await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON)
    await compute_badge_awards(session, full_reconcile=True)
    anchor_before = await _anchor(session, user, ArenaBadge.HELLO_WORLD)
    await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON + timedelta(minutes=1))

    await compute_badge_awards(session, full_reconcile=True)

    assert await _anchor(session, user, ArenaBadge.HELLO_WORLD) == anchor_before


async def test_anchoring_a_pre_existing_row_does_not_disturb_the_watermark(session: AsyncSession) -> None:
    """A reconcile that only fills anchors advances the cursor no differently."""
    user = await _new_user(session)
    problem = await _new_problem(session, await _new_user(session))
    await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON)
    await compute_badge_awards(session, full_reconcile=True)
    before = (await session.execute(select(arena_badge_cycle_state))).first()
    await _clear_anchors(session)

    await compute_badge_awards(session, full_reconcile=True)

    after = (await session.execute(select(arena_badge_cycle_state))).first()
    assert before is not None and after is not None
    assert before.last_processed_at == after.last_processed_at


async def test_unanchored_badge_is_still_evaluated_by_the_live_reconcile(session: AsyncSession) -> None:
    """The ``owned`` short-circuit skips only badges that already carry an anchor."""
    user = await _new_user(session)
    problem = await _new_problem(session, await _new_user(session))
    submission = await _submit(session, user, problem, Verdict.AC, _WEEKDAY_NOON)
    await compute_badge_awards(session, full_reconcile=True)
    await _clear_anchors(session)

    assert await compute_badge_awards(session, full_reconcile=True) == 0

    assert await _anchor(session, user, ArenaBadge.ONE_SHOT) == submission


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
