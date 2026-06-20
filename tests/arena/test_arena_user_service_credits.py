#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Unit tests for top_up_ai_credits and consume_ai_credit service functions."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_ai_credit_transactions  # noqa: F401 — register ORM mapper
import arena.models.arena_users  # noqa: F401 — register ORM mapper
from arena.models.arena_ai_credit_transactions import ArenaAiCreditTransaction
from arena.models.arena_users import ArenaUser
from arena.services.admin_user_service import get_credit_transactions_paginated
from arena.services.pagination_service import PaginationParams
from arena.services.user_ai_credit_service import consume_ai_credit, top_up_ai_credits
from shared.db_schema.arena.arena_ai_credit_transactions import arena_ai_credit_transactions
from shared.enumerations import ArenaRole


async def _make_user(
    session: AsyncSession,
    *,
    credits: int = 0,
    role: ArenaRole = ArenaRole.ARENA_USER,
) -> ArenaUser:
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome="Credits Test",
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        role=role,
        ativo=True,
        email_confirmado=True,
        session_version=0,
        precisa_trocar_senha=False,
        usa_2fa=False,
        com_foto=False,
        ai_backend_credits=credits,
        user_rating=0,
        solved_problems=0,
    )
    user.email = f"credits_{uuid.uuid4().hex[:6]}@test.example"
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


# ---------------------------------------------------------------------------
# top_up_ai_credits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_top_up_adds_credits(session: AsyncSession) -> None:
    """top_up_ai_credits increases the balance by the given quantity."""
    user = await _make_user(session, credits=3)
    result = await top_up_ai_credits(user, 5, session)
    assert result is True
    assert user.ai_backend_credits == 8


@pytest.mark.asyncio
async def test_top_up_records_transaction_with_admin(session: AsyncSession) -> None:
    """top_up_ai_credits records a positive transaction with the acting admin."""
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    user = await _make_user(session, credits=3)

    result = await top_up_ai_credits(user, 5, session, admin_id=admin.id)

    assert result is True
    tx = (
        await session.execute(select(ArenaAiCreditTransaction).where(ArenaAiCreditTransaction.user_id == user.id))
    ).scalar_one()
    assert tx.transaction_type == "topup"
    assert tx.amount == 5
    assert tx.balance_after == 8
    assert tx.admin_id == admin.id
    assert tx.submission_id is None


@pytest.mark.asyncio
async def test_top_up_zero_returns_false(session: AsyncSession) -> None:
    """top_up_ai_credits with quantity=0 returns False and leaves balance unchanged."""
    user = await _make_user(session, credits=3)
    result = await top_up_ai_credits(user, 0, session)
    assert result is False
    assert user.ai_backend_credits == 3


@pytest.mark.asyncio
async def test_top_up_negative_returns_false(session: AsyncSession) -> None:
    """top_up_ai_credits with negative quantity returns False and leaves balance unchanged."""
    user = await _make_user(session, credits=3)
    result = await top_up_ai_credits(user, -1, session)
    assert result is False
    assert user.ai_backend_credits == 3


# ---------------------------------------------------------------------------
# consume_ai_credit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consume_credit_positive_balance(session: AsyncSession) -> None:
    """consume_ai_credit decrements balance by 1 and returns True when credits > 0."""
    user = await _make_user(session, credits=5)
    result = await consume_ai_credit(user, session)
    assert result is True
    # The locked object inside consume_ai_credit is the same row — verify via DB refresh
    await session.refresh(user)
    assert user.ai_backend_credits == 4


@pytest.mark.asyncio
async def test_consume_credit_records_transaction(session: AsyncSession) -> None:
    """consume_ai_credit records a negative transaction with the balance snapshot."""
    user = await _make_user(session, credits=5)

    result = await consume_ai_credit(user, session)

    assert result is True
    tx = (
        await session.execute(select(ArenaAiCreditTransaction).where(ArenaAiCreditTransaction.user_id == user.id))
    ).scalar_one()
    assert tx.transaction_type == "consumption"
    assert tx.amount == -1
    assert tx.balance_after == 4
    assert tx.admin_id is None
    assert tx.submission_id is None


@pytest.mark.asyncio
async def test_consume_credit_last_credit(session: AsyncSession) -> None:
    """consume_ai_credit succeeds when exactly one credit remains."""
    user = await _make_user(session, credits=1)
    result = await consume_ai_credit(user, session)
    assert result is True
    await session.refresh(user)
    assert user.ai_backend_credits == 0


@pytest.mark.asyncio
async def test_consume_credit_zero_balance_returns_false(session: AsyncSession) -> None:
    """consume_ai_credit returns False without modifying balance when credits == 0."""
    user = await _make_user(session, credits=0)
    result = await consume_ai_credit(user, session)
    assert result is False
    await session.refresh(user)
    assert user.ai_backend_credits == 0


@pytest.mark.asyncio
async def test_consume_credit_negative_balance_returns_false(session: AsyncSession) -> None:
    """consume_ai_credit returns False for any non-positive balance (schema has no CHECK)."""
    user = await _make_user(session, credits=0)
    # Manually set a negative value to simulate a hypothetical inconsistency
    user.ai_backend_credits = -2
    await session.flush()
    result = await consume_ai_credit(user, session)
    assert result is False
    await session.refresh(user)
    assert user.ai_backend_credits == -2


@pytest.mark.asyncio
async def test_credit_transactions_paginated_orders_newest_first_and_clamps_page(
    session: AsyncSession,
) -> None:
    """Credit statement pagination returns the newest transactions first."""
    user = await _make_user(session, credits=0)
    base_time = datetime.now(UTC)
    for index in range(30):
        await session.execute(
            insert(arena_ai_credit_transactions).values(
                id=str(uuid.uuid4()),
                user_id=user.id,
                amount=index + 1,
                balance_after=index + 1,
                transaction_type="topup",
                created_at=base_time + timedelta(minutes=index),
            )
        )
    await session.flush()

    first_page = await get_credit_transactions_paginated(
        session,
        user.id,
        params=PaginationParams(page=1, per_page=25),
    )
    clamped_page = await get_credit_transactions_paginated(
        session,
        user.id,
        params=PaginationParams(page=99, per_page=25),
    )

    assert first_page.total == 30
    assert first_page.page == 1
    assert len(first_page.items) == 25
    assert first_page.items[0].amount == 30
    assert first_page.items[-1].amount == 6
    assert clamped_page.page == 2
    assert [tx.amount for tx in clamped_page.items] == [5, 4, 3, 2, 1]
