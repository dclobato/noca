#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena user-score rating (rate_user / rate_all_users)."""

from __future__ import annotations

import pytest
from _helpers import _make_admin, _make_judge, _make_problem, _make_user, _record_solve, _seed_rating_row
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema.arena import arena_problem_ratings, arena_users
from shared.services.arena_rating import _points_for_difficulty, rate_all_users, rate_problem, rate_user

# ---------------------------------------------------------------------------
# ARENA_JUDGE / ARENA_ADMIN exclusion from user rating
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# rate_user tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_user_no_solved_problems(session: AsyncSession) -> None:
    """A user with no solved problems should get score 0 and solved_problems 0."""
    user = await _make_user(session)

    await rate_user(session=session, user_id=user.id)

    row = (
        await session.execute(
            select(
                arena_users.c.user_rating,
                arena_users.c.solved_problems,
                arena_users.c.dta_rating_update,
            ).where(arena_users.c.id == user.id)
        )
    ).one()
    assert row.user_rating == 0
    assert row.solved_problems == 0
    assert row.dta_rating_update is not None


@pytest.mark.asyncio
async def test_rate_user_score_matches_expected_formula(session: AsyncSession) -> None:
    """User score should be the sum of exponential points per solved problem."""
    user = await _make_user(session)
    author = await _make_user(session)
    p_easy = await _make_problem(session, author)
    p_hard = await _make_problem(session, author)

    # Assign known ratings to both problems
    await _seed_rating_row(session, p_easy.id, attempted=10, solved=10, total_tries=10)
    await _seed_rating_row(session, p_hard.id, attempted=100, solved=2, total_tries=400)
    await rate_problem(session=session, problem_id=p_easy.id)
    await rate_problem(session=session, problem_id=p_hard.id)

    # User solved both
    await _record_solve(session, user.id, p_easy.id)
    await _record_solve(session, user.id, p_hard.id)

    await rate_user(session=session, user_id=user.id)

    # Fetch actual ratings applied and compute expected score
    easy_r = await session.scalar(
        select(arena_problem_ratings.c.rating).where(arena_problem_ratings.c.problem_id == p_easy.id)
    )
    hard_r = await session.scalar(
        select(arena_problem_ratings.c.rating).where(arena_problem_ratings.c.problem_id == p_hard.id)
    )
    assert easy_r is not None and hard_r is not None
    expected = round(_points_for_difficulty(easy_r) + _points_for_difficulty(hard_r))

    row = await session.scalar(select(arena_users.c.user_rating).where(arena_users.c.id == user.id))
    assert row == expected


# ---------------------------------------------------------------------------
# rate_all_users tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_all_users_updates_dta_rating_update(session: AsyncSession) -> None:
    """rate_all_users must touch every arena_users row."""
    u1 = await _make_user(session)
    u2 = await _make_user(session)

    count = await rate_all_users(session)

    assert count >= 2
    for uid in (u1.id, u2.id):
        row = await session.scalar(select(arena_users.c.dta_rating_update).where(arena_users.c.id == uid))
        assert row is not None, f"dta_rating_update not set for user {uid}"


@pytest.mark.asyncio
async def test_rate_all_users_excludes_judge_and_admin(session: AsyncSession) -> None:
    """rate_all_users must not rate ARENA_JUDGE or ARENA_ADMIN accounts."""
    judge = await _make_judge(session)
    admin = await _make_admin(session)

    await rate_all_users(session)

    for uid, label in ((judge.id, "judge"), (admin.id, "admin")):
        row = await session.scalar(select(arena_users.c.dta_rating_update).where(arena_users.c.id == uid))
        assert row is None, f"dta_rating_update was set for {label} — should have been skipped"
