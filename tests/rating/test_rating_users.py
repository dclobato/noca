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


@pytest.mark.asyncio
async def test_rate_user_excludes_owned_problem_solves(session: AsyncSession) -> None:
    """User score must ignore solves for problems owned by that same user."""
    user = await _make_user(session)
    other_author = await _make_user(session)
    owned = await _make_problem(session, user)
    other = await _make_problem(session, other_author)
    await _seed_rating_row(session, owned.id, attempted=10, solved=10, total_tries=10)
    await _seed_rating_row(session, other.id, attempted=10, solved=10, total_tries=10)
    await rate_problem(session=session, problem_id=owned.id)
    await rate_problem(session=session, problem_id=other.id)
    await _record_solve(session, user.id, owned.id)
    await _record_solve(session, user.id, other.id)

    await rate_user(session=session, user_id=user.id)

    other_rating = await session.scalar(
        select(arena_problem_ratings.c.rating).where(arena_problem_ratings.c.problem_id == other.id)
    )
    assert other_rating is not None
    expected = round(_points_for_difficulty(other_rating))

    row = (
        await session.execute(
            select(arena_users.c.user_rating, arena_users.c.solved_problems).where(arena_users.c.id == user.id)
        )
    ).one()
    assert row.user_rating == expected
    assert row.solved_problems == 1


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
async def test_rate_all_users_rates_judge_and_admin(session: AsyncSession) -> None:
    """rate_all_users must rate ARENA_JUDGE and ARENA_ADMIN accounts."""
    author = await _make_user(session)
    problem = await _make_problem(session, author)
    await _seed_rating_row(session, problem.id, attempted=10, solved=10, total_tries=10)
    judge = await _make_judge(session)
    admin = await _make_admin(session)
    await _record_solve(session, judge.id, problem.id)
    await _record_solve(session, admin.id, problem.id)

    await rate_all_users(session)

    for uid, label in ((judge.id, "judge"), (admin.id, "admin")):
        row = (
            await session.execute(
                select(
                    arena_users.c.user_rating,
                    arena_users.c.solved_problems,
                    arena_users.c.dta_rating_update,
                ).where(arena_users.c.id == uid)
            )
        ).one()
        assert row.user_rating > 0, f"user_rating was not set for {label}"
        assert row.solved_problems == 1, f"solved_problems was not set for {label}"
        assert row.dta_rating_update is not None, f"dta_rating_update was not set for {label}"
