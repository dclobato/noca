#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for Arena leaderboard service queries."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_users  # noqa: F401
from arena.models.arena_users import ArenaUser
from arena.services.leaderboard_service import get_top_rated_users
from shared.enumerations import ArenaRole


async def _make_user(
    session: AsyncSession,
    *,
    name: str,
    rating: int | None,
    solved: int | None,
    role: ArenaRole = ArenaRole.ARENA_USER,
    active: bool = True,
    confirmed: bool = True,
    created_at: datetime | None = None,
) -> ArenaUser:
    """Create a persisted Arena user with leaderboard-relevant fields."""
    user = ArenaUser(
        nome=name,
        email_normalizado=f"{name.lower().replace(' ', '.')}@example.com",
        dta_nascimento=date(2000, 1, 1),
        role=role,
        ativo=active,
        email_confirmado=confirmed,
        consentimento_responsavel=True,
        user_rating=rating,
        solved_problems=solved,
    )
    if created_at is not None:
        user.created_at = created_at
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_get_top_rated_users_orders_by_rating_and_tiebreakers(
    session: AsyncSession,
) -> None:
    """Top users should rank by rating and order ties by confidence and creation date."""
    now = datetime.now(UTC)
    await _make_user(session, name="Delta", rating=90, solved=3)
    await _make_user(session, name="Bravo", rating=100, solved=2, created_at=now)
    await _make_user(
        session,
        name="Charlie",
        rating=100,
        solved=5,
        created_at=now + timedelta(minutes=1),
    )
    await _make_user(
        session,
        name="Alpha",
        rating=100,
        solved=5,
        created_at=now - timedelta(minutes=1),
    )

    top_users = await get_top_rated_users(session, limit=3)

    assert [user.name for user in top_users] == ["Alpha", "Charlie", "Bravo"]
    assert [user.rank for user in top_users] == [1, 1, 1]
    assert [user.rating for user in top_users] == [100, 100, 100]
    assert [user.confidence for user in top_users] == [39, 39, 18]
    assert [user.solved_problems for user in top_users] == [5, 5, 2]


@pytest.mark.asyncio
async def test_get_top_rated_users_uses_competition_rank_for_rating_ties(
    session: AsyncSession,
) -> None:
    """Users with the same rating should share a displayed leaderboard rank."""
    await _make_user(session, name="First", rating=200, solved=10)
    await _make_user(session, name="Tied One", rating=100, solved=5)
    await _make_user(session, name="Tied Two", rating=100, solved=4)
    await _make_user(session, name="Fourth", rating=50, solved=2)

    top_users = await get_top_rated_users(session, limit=4)

    assert [user.name for user in top_users] == ["First", "Tied One", "Tied Two", "Fourth"]
    assert [user.rank for user in top_users] == [1, 2, 2, 4]


@pytest.mark.asyncio
async def test_get_top_rated_users_filters_staff_inactive_and_unconfirmed(
    session: AsyncSession,
) -> None:
    """Leaderboard should only include active confirmed regular Arena users."""
    await _make_user(session, name="Visible", rating=50, solved=2)
    await _make_user(session, name="Inactive", rating=100, solved=3, active=False)
    await _make_user(session, name="Unconfirmed", rating=90, solved=3, confirmed=False)
    await _make_user(
        session,
        name="Judge",
        rating=80,
        solved=3,
        role=ArenaRole.ARENA_JUDGE,
    )

    top_users = await get_top_rated_users(session, limit=5)

    assert [user.name for user in top_users] == ["Visible"]


@pytest.mark.asyncio
async def test_get_top_rated_users_limit_below_one_returns_empty(
    session: AsyncSession,
) -> None:
    """Non-positive top-k limits should not query for leaderboard rows."""
    await _make_user(session, name="Visible", rating=50, solved=2)

    assert await get_top_rated_users(session, limit=0) == []
