#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena signup reputation recording service."""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_users  # noqa: F401
from arena.models.arena_user_reputation import ArenaUserReputation
from arena.models.arena_users import ArenaUser
from arena.services.signup_reputation_service import record_signup_reputation
from shared.enumerations import ArenaRole
from shared.services.email_reputation import EmailReputation
from shared.services.network_utils.ip_reputation import IPReputation

_IP_REP = IPReputation(
    is_crawler=False,
    mobile=True,
    recent_abuse=True,
    fraud_score=88,
    proxy=True,
    vpn=True,
    tor=False,
    active_vpn=True,
    active_tor=False,
)
_EMAIL_REP = EmailReputation(
    valid=True,
    disposable=True,
    suspect=False,
    overall_score=3,
    common=False,
    fraud_score=42,
    sanitized_email="user@test.example",
)


async def _make_user(session: AsyncSession, *, role: ArenaRole = ArenaRole.ARENA_USER) -> ArenaUser:
    """Create and persist an Arena user."""
    user = ArenaUser(
        nome=f"Rep User {uuid.uuid4().hex[:6]}",
        email_normalizado=f"rep-{uuid.uuid4().hex[:8]}@test.example",
        dta_nascimento=date(2000, 1, 1),
        role=role,
        ativo=True,
        email_confirmado=True,
        consentimento_responsavel=True,
    )
    user.password = "Senha@Forte1!"
    session.add(user)
    await session.flush()
    return user


def _fake_service(return_value: object) -> MagicMock:
    """Return a fake reputation service whose check() returns a fixed value."""
    return MagicMock(check=MagicMock(return_value=return_value))


def _mock_email_service() -> MagicMock:
    """Return a mock EmailService recording send_email calls."""
    return MagicMock(send_email=MagicMock(return_value=MagicMock(success=True)))


async def _get_row(session: AsyncSession, user_id: str) -> ArenaUserReputation | None:
    return await session.scalar(select(ArenaUserReputation).where(ArenaUserReputation.user_id == user_id))


@pytest.mark.asyncio
async def test_records_reputation_and_notifies_admins(session: AsyncSession) -> None:
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    user = await _make_user(session)
    email_service = _mock_email_service()

    await record_signup_reputation(
        session,
        user_id=user.id,
        email=user.email_normalizado,
        ip_address="203.0.113.10",
        ip_service=_fake_service(_IP_REP),
        email_reputation_service=_fake_service(_EMAIL_REP),
        email_service=email_service,
        reputation_enabled=True,
    )

    row = await _get_row(session, user.id)
    assert row is not None
    assert row.signup_ip == "203.0.113.10"
    assert row.ip_fraud_score == 88
    assert row.ip_report["proxy"] is True
    assert row.ip_checked_at is not None
    assert row.email_fraud_score == 42
    assert row.email_overall_score == 3
    assert row.email_report["disposable"] is True
    assert row.email_checked_at is not None

    email_service.send_email.assert_called_once()
    assert email_service.send_email.call_args.kwargs["to_email"] == admin.email_normalizado


@pytest.mark.asyncio
async def test_disabled_records_ip_only_and_sends_no_email(session: AsyncSession) -> None:
    await _make_user(session, role=ArenaRole.ARENA_ADMIN)
    user = await _make_user(session)
    ip_service = _fake_service(_IP_REP)
    email_rep_service = _fake_service(_EMAIL_REP)
    email_service = _mock_email_service()

    await record_signup_reputation(
        session,
        user_id=user.id,
        email=user.email_normalizado,
        ip_address="203.0.113.11",
        ip_service=ip_service,
        email_reputation_service=email_rep_service,
        email_service=email_service,
        reputation_enabled=False,
    )

    row = await _get_row(session, user.id)
    assert row is not None
    assert row.signup_ip == "203.0.113.11"
    assert row.ip_report is None
    assert row.email_report is None
    ip_service.check.assert_not_called()
    email_rep_service.check.assert_not_called()
    email_service.send_email.assert_not_called()


@pytest.mark.asyncio
async def test_second_run_updates_same_row(session: AsyncSession) -> None:
    user = await _make_user(session)
    email_service = _mock_email_service()

    # First: IP recorded while disabled.
    await record_signup_reputation(
        session,
        user_id=user.id,
        email=user.email_normalizado,
        ip_address="203.0.113.12",
        ip_service=_fake_service(_IP_REP),
        email_reputation_service=_fake_service(_EMAIL_REP),
        email_service=email_service,
        reputation_enabled=False,
    )
    # Second: reputation later computed for the same user.
    await record_signup_reputation(
        session,
        user_id=user.id,
        email=user.email_normalizado,
        ip_address="203.0.113.12",
        ip_service=_fake_service(_IP_REP),
        email_reputation_service=_fake_service(_EMAIL_REP),
        email_service=email_service,
        reputation_enabled=True,
    )

    rows = (await session.scalars(select(ArenaUserReputation).where(ArenaUserReputation.user_id == user.id))).all()
    assert len(rows) == 1
    assert rows[0].ip_report is not None
    assert rows[0].email_report is not None


@pytest.mark.asyncio
async def test_no_admins_still_records_row(session: AsyncSession) -> None:
    user = await _make_user(session)
    email_service = _mock_email_service()

    await record_signup_reputation(
        session,
        user_id=user.id,
        email=user.email_normalizado,
        ip_address="203.0.113.13",
        ip_service=_fake_service(_IP_REP),
        email_reputation_service=_fake_service(_EMAIL_REP),
        email_service=email_service,
        reputation_enabled=True,
    )

    row = await _get_row(session, user.id)
    assert row is not None
    email_service.send_email.assert_not_called()
