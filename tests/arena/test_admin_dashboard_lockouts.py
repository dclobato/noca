#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the Arena dashboard lockouts page (``/admin/dashboard/lockouts``).

The decisions under test:

- an IP unlock lifts every Arena action for that address and only that
  address, never a Web bucket for the same address
- an account unlock resolves a known address to the account's full recipe,
  hashes an unknown one as typed, and honours an explicit event hash
- a prefilled subject renders its live status; a malformed one is refused
- every outcome is audited with a target that never names an unresolved
  identifier in clear
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.models.arena_users import ArenaUser
from shared.enumerations import ArenaRole
from shared.services.auth_rate_limit import hash_identifier
from tests.arena._lockout_helpers import admin_actions, is_locked, seed_lock
from tests.arena.test_admin_users import _TEST_PASSWORD, _build_admin_app, _create_arena_user, _login_token
from tests.shared._auth_fake_valkey import AuthFakeValkey

_IP = "203.0.113.9"
_PAGE = "/admin/dashboard/lockouts"


async def _setup(session: AsyncSession) -> tuple[FastAPI, AuthFakeValkey, ArenaUser]:
    app = _build_admin_app(session)
    valkey = AuthFakeValkey()
    app.state.valkey_runtime = valkey
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    return app, valkey, admin


def _client(app: FastAPI, admin: ArenaUser) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, admin)},
    )


async def _post(app: FastAPI, admin: ArenaUser, path: str, **data: str) -> Response:
    async with _client(app, admin) as client:
        return await client.post(path, data={"confirm_password": _TEST_PASSWORD, **data}, follow_redirects=False)


@pytest.mark.asyncio
async def test_page_renders_the_status_of_a_prefilled_address(session: AsyncSession) -> None:
    app, valkey, admin = await _setup(session)
    seed_lock(valkey, module="arena", action="login", ip=_IP)

    async with _client(app, admin) as client:
        plain = await client.get(_PAGE)
        prefilled = await client.get(_PAGE, params={"ip": _IP})
        malformed = await client.get(_PAGE, params={"ip": "not-an-address"})

    assert plain.status_code == 200
    assert "Status for" not in plain.text
    assert f'value="{_IP}"' in prefilled.text
    assert "arena/login" in prefilled.text
    assert "lifts in 10 min" in prefilled.text
    assert "Enter one IPv4 or IPv6 address." in malformed.text


@pytest.mark.asyncio
async def test_unlock_ip_lifts_every_arena_action_of_that_address_only(session: AsyncSession) -> None:
    app, valkey, admin = await _setup(session)
    seed_lock(valkey, module="arena", action="login", ip=_IP)
    seed_lock(valkey, module="arena", action="google-login", ip=_IP)
    seed_lock(valkey, module="arena", action="login", ip="198.51.100.7")
    seed_lock(valkey, module="web", action="login", ip=_IP)

    response = await _post(app, admin, f"{_PAGE}/unlock-ip", ip=f" {_IP} ")

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"{_PAGE}?ip={_IP}")
    assert not is_locked(valkey, module="arena", action="login", ip=_IP)
    assert not is_locked(valkey, module="arena", action="google-login", ip=_IP)
    assert is_locked(valkey, module="arena", action="login", ip="198.51.100.7")
    assert is_locked(valkey, module="web", action="login", ip=_IP), "Web buckets are not Arena's to lift"
    audit = await admin_actions(session, "unlock_ip")
    assert len(audit) == 1
    assert (audit[0]["target_type"], audit[0]["target_id"]) == ("client_ip", _IP)
    assert "actions=arena/google-login,arena/login" in audit[0]["detail"]


@pytest.mark.asyncio
async def test_unlock_ip_refuses_a_malformed_address_and_the_unknown_sentinel(session: AsyncSession) -> None:
    app, valkey, admin = await _setup(session)
    seed_lock(valkey, module="arena", action="login", ip="unknown")

    for bad in ("unknown", "203.0.113.0/24"):
        response = await _post(app, admin, f"{_PAGE}/unlock-ip", ip=bad)
        assert response.status_code == 303

    assert is_locked(valkey, module="arena", action="login", ip="unknown")
    assert await admin_actions(session, "unlock_ip") == []


@pytest.mark.asyncio
async def test_unlock_account_resolves_a_known_address_to_the_whole_recipe(session: AsyncSession) -> None:
    app, valkey, admin = await _setup(session)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    seed_lock(valkey, module="arena", action="login", identifier="target@test.example")
    seed_lock(valkey, module="arena", action="password_verify", identifier=target.id)

    response = await _post(app, admin, f"{_PAGE}/unlock-account", identifier="Target@Test.example")

    assert response.status_code == 303
    assert not is_locked(valkey, module="arena", action="login", identifier="target@test.example")
    assert not is_locked(valkey, module="arena", action="password_verify", identifier=target.id)
    audit = await admin_actions(session, "unlock_account")
    assert (audit[0]["target_type"], audit[0]["target_id"]) == ("arena_user", target.id)


@pytest.mark.asyncio
async def test_unlock_account_hashes_an_unknown_address_as_typed(session: AsyncSession) -> None:
    app, valkey, admin = await _setup(session)
    seed_lock(valkey, module="arena", action="login", identifier="nobody@test.example")

    response = await _post(app, admin, f"{_PAGE}/unlock-account", identifier="NoBody@test.example")

    assert response.status_code == 303
    assert not is_locked(valkey, module="arena", action="login", identifier="nobody@test.example")
    audit = await admin_actions(session, "unlock_account")
    assert audit[0]["target_type"] == "identifier_hash"
    assert "nobody" not in audit[0]["target_id"], "an unresolved identifier is never audited in clear"
    assert audit[0]["target_id"] == hash_identifier("nobody@test.example", secret=settings.JWT_SECRET_KEY)[:12]


@pytest.mark.asyncio
async def test_unlock_account_honours_an_explicit_event_hash(session: AsyncSession) -> None:
    """The security-event viewer links here with the exact hash the throttle recorded."""
    app, valkey, admin = await _setup(session)
    seed_lock(valkey, module="arena", action="2fa", identifier="someone@test.example")
    digest = hash_identifier("someone@test.example", secret=settings.JWT_SECRET_KEY)

    async with _client(app, admin) as client:
        page = await client.get(_PAGE, params={"identifier_hash": digest})
    assert "arena/2fa" in page.text

    response = await _post(app, admin, f"{_PAGE}/unlock-account", identifier_hash=digest)

    assert response.status_code == 303
    assert not is_locked(valkey, module="arena", action="2fa", identifier="someone@test.example")


@pytest.mark.asyncio
async def test_unlock_account_with_nothing_usable_is_refused(session: AsyncSession) -> None:
    app, _valkey, admin = await _setup(session)

    response = await _post(app, admin, f"{_PAGE}/unlock-account", identifier="   ", identifier_hash="zz")

    assert response.status_code == 303
    assert await admin_actions(session, "unlock_account") == []


@pytest.mark.asyncio
async def test_security_event_rows_link_to_the_page_with_their_subject(session: AsyncSession) -> None:
    from arena.routes.admin_dashboard_security import router as security_router
    from shared.services.security_events import record_security_event

    app, _valkey, admin = await _setup(session)
    app.router.routes = [
        r for r in app.router.routes if getattr(r, "name", None) != "arena_admin_dashboard_security_events"
    ]
    app.include_router(security_router)
    digest = hash_identifier("locked@test.example", secret=settings.JWT_SECRET_KEY)
    await record_security_event(
        session,
        module="arena",
        event_type="auth_throttle_lockout",
        actor_label="locked@test.example",
        identifier_hash=digest,
        client_ip=_IP,
    )
    await session.commit()

    async with _client(app, admin) as client:
        response = await client.get("/admin/dashboard/security-events")

    assert response.status_code == 200
    assert f"/admin/dashboard/lockouts?ip={_IP}" in response.text
    assert f"identifier=locked%40test.example&amp;identifier_hash={digest}" in response.text
