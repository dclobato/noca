#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for ranking_visible exclusion in affiliation rating computation."""

from __future__ import annotations

import uuid

import pytest
from _helpers import _make_admin, _make_judge, _make_user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_affiliations  # noqa: F401
import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.models.arena_affiliations import ArenaAffiliation
from shared.db_schema.arena import arena_affiliations, arena_users
from shared.services.arena_rating import rate_affiliation

_DECAY_FACTOR = 5.0


async def _make_affiliation(session: AsyncSession, name: str | None = None) -> ArenaAffiliation:
    affiliation = ArenaAffiliation(
        name=name or f"Affiliation {uuid.uuid4().hex[:8]}",
    )
    session.add(affiliation)
    await session.flush()
    return affiliation


async def _set_rating(session: AsyncSession, user_id: str, rating: int) -> None:
    await session.execute(arena_users.update().where(arena_users.c.id == user_id).values(user_rating=rating))
    await session.flush()


async def _set_affiliation(session: AsyncSession, user_id: str, affiliation_id: str) -> None:
    await session.execute(arena_users.update().where(arena_users.c.id == user_id).values(affiliation_id=affiliation_id))
    await session.flush()


async def _set_ranking_visible(session: AsyncSession, user_id: str, *, visible: bool) -> None:
    await session.execute(arena_users.update().where(arena_users.c.id == user_id).values(ranking_visible=visible))
    await session.flush()


# ---------------------------------------------------------------------------
# Affiliation rating excludes hidden members
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hidden_rating_excluded_from_affiliation_computation(session: AsyncSession) -> None:
    """rate_affiliation() must exclude members whose ranking_visible=False."""
    aff = await _make_affiliation(session)

    # One visible member with a high rating
    visible = await _make_user(session)
    await _set_rating(session, visible.id, 500)
    await _set_affiliation(session, visible.id, aff.id)

    # One hidden member with an even higher rating
    hidden = await _make_user(session)
    await _set_rating(session, hidden.id, 1000)
    await _set_affiliation(session, hidden.id, aff.id)
    await _set_ranking_visible(session, hidden.id, visible=False)

    # Compute with hidden user excluded
    await rate_affiliation(session=session, affiliation_id=aff.id, f=_DECAY_FACTOR)
    await session.flush()

    rating_with_hidden_excluded = await session.scalar(
        select(arena_affiliations.c.rating).where(arena_affiliations.c.id == aff.id)
    )

    # Now make the hidden user visible and recompute
    await _set_ranking_visible(session, hidden.id, visible=True)
    await rate_affiliation(session=session, affiliation_id=aff.id, f=_DECAY_FACTOR)
    await session.flush()

    rating_with_all_visible = await session.scalar(
        select(arena_affiliations.c.rating).where(arena_affiliations.c.id == aff.id)
    )

    # With the high-rated hidden member excluded, affiliation rating must be lower
    assert rating_with_hidden_excluded is not None
    assert rating_with_all_visible is not None
    assert rating_with_hidden_excluded < rating_with_all_visible


@pytest.mark.asyncio
async def test_affiliation_with_all_members_hidden_rates_zero(session: AsyncSession) -> None:
    """An affiliation where all members are hidden should rate as 0."""
    aff = await _make_affiliation(session)

    for i in range(3):
        user = await _make_user(session)
        await _set_rating(session, user.id, 100 * (i + 1))
        await _set_affiliation(session, user.id, aff.id)
        await _set_ranking_visible(session, user.id, visible=False)

    await rate_affiliation(session=session, affiliation_id=aff.id, f=_DECAY_FACTOR)
    await session.flush()

    rating = await session.scalar(select(arena_affiliations.c.rating).where(arena_affiliations.c.id == aff.id))
    assert rating == 0


@pytest.mark.asyncio
async def test_affiliation_excludes_judge_and_admin_members(session: AsyncSession) -> None:
    """rate_affiliation() must not count ARENA_JUDGE or ARENA_ADMIN members."""
    aff = await _make_affiliation(session)

    # Regular user with a known rating
    user = await _make_user(session)
    await _set_rating(session, user.id, 200)
    await _set_affiliation(session, user.id, aff.id)

    # Judge with a much higher rating — must be ignored
    judge = await _make_judge(session)
    await _set_rating(session, judge.id, 5000)
    await _set_affiliation(session, judge.id, aff.id)

    # Admin with a much higher rating — must be ignored
    admin = await _make_admin(session)
    await _set_rating(session, admin.id, 5000)
    await _set_affiliation(session, admin.id, aff.id)

    await rate_affiliation(session=session, affiliation_id=aff.id, f=_DECAY_FACTOR)
    await session.flush()

    result = await session.scalar(select(arena_affiliations.c.rating).where(arena_affiliations.c.id == aff.id))

    # Only the ARENA_USER (rating=200) should contribute
    expected = round((1.0 / _DECAY_FACTOR) * 200)
    assert result == expected


@pytest.mark.asyncio
async def test_fully_visible_affiliation_uses_all_members(session: AsyncSession) -> None:
    """An affiliation with all members visible should compute the expected rating."""
    aff = await _make_affiliation(session)

    ratings = [400, 300, 200]
    for r in ratings:
        user = await _make_user(session)
        await _set_rating(session, user.id, r)
        await _set_affiliation(session, user.id, aff.id)

    await rate_affiliation(session=session, affiliation_id=aff.id, f=_DECAY_FACTOR)
    await session.flush()

    result = await session.scalar(select(arena_affiliations.c.rating).where(arena_affiliations.c.id == aff.id))

    # Manual formula: S = (1/5)*400 + (4/5)*(1/5)*300 + (4/5)^2*(1/5)*200
    w = 1.0 / _DECAY_FACTOR
    decay = 1.0 - w
    expected = round(w * 400 + decay * w * 300 + decay**2 * w * 200)
    assert result == expected
