#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the reputation backfill script's DB-facing helpers."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_users  # noqa: F401
from arena.models.arena_user_reputation import ArenaUserReputation
from arena.models.arena_users import ArenaUser
from scripts.backfill_email_reputation import (
    _targets,
    _update_ip_reputation,
    _upsert_email_reputation,
)
from shared.db_schema.arena import arena_user_reputation
from shared.enumerations import ArenaRole
from shared.services.email_reputation import EmailReputation
from shared.services.network_utils.ip_reputation import IPReputation

_EMAIL_REP = EmailReputation(
    valid=True,
    disposable=False,
    suspect=False,
    overall_score=4,
    common=False,
    fraud_score=7,
    sanitized_email="user@test.example",
)
_IP_REP = IPReputation(
    is_crawler=False,
    mobile=False,
    recent_abuse=False,
    fraud_score=13,
    proxy=True,
    vpn=False,
    tor=False,
    active_vpn=False,
    active_tor=False,
)


async def _make_user(session: AsyncSession) -> ArenaUser:
    user = ArenaUser(
        nome=f"Backfill User {uuid.uuid4().hex[:6]}",
        email_normalizado=f"bf-{uuid.uuid4().hex[:8]}@test.example",
        dta_nascimento=date(2000, 1, 1),
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        consentimento_responsavel=True,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_targets_selects_user_without_row(session: AsyncSession) -> None:
    user = await _make_user(session)
    targets = await _targets(session)
    match = [t for t in targets if t.user_id == user.id]
    assert len(match) == 1
    assert match[0].needs_email is True
    assert match[0].needs_ip is False
    assert match[0].signup_ip is None


@pytest.mark.asyncio
async def test_upsert_email_inserts_then_targets_excludes(session: AsyncSession) -> None:
    user = await _make_user(session)

    await _upsert_email_reputation(session, user.id, _EMAIL_REP)
    await session.flush()

    row = await session.scalar(
        text("SELECT email_fraud_score FROM arena_user_reputation WHERE user_id = :u").bindparams(u=user.id)
    )
    assert row == 7
    # Idempotent: once the email report is stored, the user is no longer a target.
    assert all(t.user_id != user.id for t in await _targets(session))


@pytest.mark.asyncio
async def test_recorded_ip_without_report_is_ip_target(session: AsyncSession) -> None:
    user = await _make_user(session)
    session.add(ArenaUserReputation(user_id=user.id, signup_ip="203.0.113.7"))
    await session.flush()

    targets = await _targets(session)
    match = [t for t in targets if t.user_id == user.id]
    assert len(match) == 1
    assert match[0].needs_ip is True
    assert match[0].signup_ip == "203.0.113.7"

    await _update_ip_reputation(session, user.id, _IP_REP)

    row = (
        await session.execute(
            select(arena_user_reputation.c.ip_fraud_score, arena_user_reputation.c.ip_report).where(
                arena_user_reputation.c.user_id == user.id
            )
        )
    ).one()
    assert row.ip_fraud_score == 13
    assert row.ip_report["proxy"] is True
