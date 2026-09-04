#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Admin parental-consent toggle: effect parity, audit, and password confirmation.

The point of these tests is that an admin revocation is **not weaker** than a guardian
revocation, so the effect is asserted through the very same ``assert_revocation_effect``
helper ``test_parental_consent_revoke.py`` uses. If one path is ever changed without the
other, one of the two modules fails.

Kept out of ``test_admin_users.py`` (1997 lines) so neither file passes the ``AGENTS.md``
size bound.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_users  # noqa: F401
from arena.services import parental_consent_service
from shared.db_schema import security_events
from shared.enumerations import ArenaRole
from tests.arena._parental_consent_helpers import (
    assert_revocation_effect,
    create_consented_minor,
    revocation_token_for,
    security_event_types,
)
from tests.arena.test_admin_users import (
    _TEST_PASSWORD,
    _build_admin_app,
    _create_arena_user,
    _login_token,
)

_REVOKE_PATH = "/auth/parental-consent/revoke"


async def _admin_actions(session: AsyncSession, target_id: str) -> list[str]:
    """Return the admin-audit action names recorded against a target account."""
    result = await session.execute(
        select(security_events.c.metadata)
        .where(
            security_events.c.module == "arena",
            security_events.c.event_type == "admin_action",
            security_events.c.actor_user_id.is_not(None),
        )
        .order_by(security_events.c.id)
    )
    return [str((payload or {}).get("action", "")) for payload in result.scalars()]


@pytest.mark.asyncio
async def test_admin_revocation_has_the_same_effect_as_the_guardian_flow(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await create_consented_minor(session)
    before = (target.session_version, target.consent_generation, target.ranking_visible)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/toggle-parental-consent",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    await session.refresh(target)
    assert response.status_code == 303
    assert_revocation_effect(
        target,
        session_version_before=before[0],
        consent_generation_before=before[1],
        ranking_visible_before=before[2],
    )
    events = await security_event_types(session, target.id)
    assert "parental_consent_revoked" in events
    assert "account_deactivated" in events


@pytest.mark.asyncio
async def test_admin_revocation_invalidates_an_outstanding_guardian_link(session: AsyncSession) -> None:
    """A link already sitting in the guardian's mailbox stops working."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await create_consented_minor(session)
    guardian_link_token = revocation_token_for(target, app.state.jwt_service)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        await client.post(
            f"/admin/users/{target.id}/toggle-parental-consent",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    await session.refresh(target)
    resolved = await parental_consent_service.resolve_revocation_token(
        guardian_link_token, session, app.state.jwt_service
    )
    assert resolved.valid is False
    assert resolved.log_reason == "stale_generation"


@pytest.mark.asyncio
async def test_admin_grant_restores_a_suspended_account(session: AsyncSession) -> None:
    """Granting is a real recovery: the account is usable again, not merely flagged."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await create_consented_minor(session)
    target.consentimento_responsavel = False
    target.dta_consentimento_responsavel = None
    target.ativo = False
    await session.commit()
    generation_before = target.consent_generation
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        await client.post(
            f"/admin/users/{target.id}/toggle-parental-consent",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    await session.refresh(target)
    assert target.consentimento_responsavel is True
    assert target.ativo is True
    assert target.consent_generation == generation_before + 1
    events = await security_event_types(session, target.id)
    assert "parental_consent_granted" in events
    assert "account_activated" in events


@pytest.mark.asyncio
async def test_admin_grant_does_not_activate_an_account_with_an_unconfirmed_email(session: AsyncSession) -> None:
    """The consent gate is not the only gate; granting must not skip the others."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await create_consented_minor(session)
    target.consentimento_responsavel = False
    target.dta_consentimento_responsavel = None
    target.ativo = False
    target.email_confirmado = False
    await session.commit()
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        await client.post(
            f"/admin/users/{target.id}/toggle-parental-consent",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    await session.refresh(target)
    assert target.consentimento_responsavel is True
    assert target.ativo is False
    assert "account_activated" not in await security_event_types(session, target.id)


@pytest.mark.asyncio
async def test_the_toggle_is_audited(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await create_consented_minor(session)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        await client.post(
            f"/admin/users/{target.id}/toggle-parental-consent",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    assert "parental_consent_revoked" in await _admin_actions(session, target.id)


@pytest.mark.asyncio
async def test_the_toggle_requires_the_admin_password(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await create_consented_minor(session)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            f"/admin/users/{target.id}/toggle-parental-consent",
            data={"confirm_password": "WrongPass1!"},
            follow_redirects=False,
        )

    await session.refresh(target)
    assert response.status_code == 303
    assert target.consentimento_responsavel is True
    assert target.ativo is True
    assert "parental_consent_revoked" not in await _admin_actions(session, target.id)


@pytest.mark.asyncio
async def test_a_revoked_account_recovers_through_a_fresh_guardian_grant(session: AsyncSession) -> None:
    """End to end: guardian revokes, admin restores, and a new link works again."""
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await create_consented_minor(session)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        await client.post(
            f"/admin/users/{target.id}/toggle-parental-consent",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )
        await session.refresh(target)
        assert target.ativo is False

        await client.post(
            f"/admin/users/{target.id}/toggle-parental-consent",
            data={"confirm_password": _TEST_PASSWORD},
            follow_redirects=False,
        )

    await session.refresh(target)
    assert target.ativo is True
    resolved = await parental_consent_service.resolve_revocation_token(
        revocation_token_for(target, app.state.jwt_service), session, app.state.jwt_service
    )
    assert resolved.valid is True
