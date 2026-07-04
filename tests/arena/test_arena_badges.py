#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM tests for the Arena user badge ledger."""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from arena.models.arena_badges import ArenaUserBadge
from arena.models.arena_users import ArenaUser
from shared.enumerations import ArenaBadge, ArenaRole


async def _create_user(session: AsyncSession) -> ArenaUser:
    """Persist a minimal Arena user for badge tests.

    Args:
        session: Async database session.

    Returns:
        ArenaUser: Flushed user ready to own badges.
    """
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome="Badge User",
        email_normalizado=f"{uuid.uuid4()}@test.example",
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        aceitou_termos_privacidade=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=1,
        prefered_language="en-US",
    )
    user.password = "StrongPass1!"
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_badge_relationship_round_trips(session: AsyncSession) -> None:
    """Badges added to a user load back through the relationship."""
    user = await _create_user(session)
    awarded = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    session.add(
        ArenaUserBadge(
            id=str(uuid.uuid4()),
            user_id=user.id,
            badge=ArenaBadge.HELLO_WORLD,
            awarded_at=awarded,
        )
    )
    session.add(
        ArenaUserBadge(
            id=str(uuid.uuid4()),
            user_id=user.id,
            badge=ArenaBadge.ONE_SHOT,
            awarded_at=awarded,
        )
    )
    await session.flush()

    await session.refresh(user, ["badges"])
    assert {b.badge for b in user.badges} == {ArenaBadge.HELLO_WORLD, ArenaBadge.ONE_SHOT}

    loaded = await session.scalar(
        select(ArenaUser).where(ArenaUser.id == user.id).options(selectinload(ArenaUser.badges))
    )
    assert loaded is not None
    assert all(b.user_id == user.id for b in loaded.badges)


@pytest.mark.asyncio
async def test_badge_is_unique_per_user(session: AsyncSession) -> None:
    """The same badge cannot be awarded to one user twice."""
    user = await _create_user(session)
    awarded = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    session.add(
        ArenaUserBadge(
            id=str(uuid.uuid4()),
            user_id=user.id,
            badge=ArenaBadge.FULL_CLEAR,
            awarded_at=awarded,
        )
    )
    await session.flush()
    session.add(
        ArenaUserBadge(
            id=str(uuid.uuid4()),
            user_id=user.id,
            badge=ArenaBadge.FULL_CLEAR,
            awarded_at=awarded,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
