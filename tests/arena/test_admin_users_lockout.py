#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the per-user sign-in unlock (``POST /admin/users/{id}/unlock``).

The decisions under test:

- the unlock reaches every Arena bucket the account can be locked in --
  the login form's email, the user id the password re-verification keys on,
  and the ``sub:`` subjects of the emailed links -- and nothing else
- the address the failures came from is deliberately left alone
- it is password-confirmed and audited as ``unlock_account``
- the profile's security tab shows the live status before and after
- when the shared store cannot answer, the outcome is stated and audited
  rather than reported as done
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser
from shared.enumerations import ArenaRole
from tests.arena._lockout_helpers import admin_actions, is_locked, seed_lock
from tests.arena.test_admin_users import _TEST_PASSWORD, _build_admin_app, _create_arena_user, _login_token
from tests.shared._auth_fake_valkey import AuthFakeValkey

_IP = "203.0.113.9"


async def _setup(session: AsyncSession) -> tuple[FastAPI, AuthFakeValkey, ArenaUser, ArenaUser]:
    app = _build_admin_app(session)
    valkey = AuthFakeValkey()
    app.state.valkey_runtime = valkey
    admin = await _create_arena_user(session, name="Admin", email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    target = await _create_arena_user(session, name="Target", email="target@test.example")
    return app, valkey, admin, target


def _seed_target_locks(valkey: AuthFakeValkey, target: ArenaUser) -> None:
    seed_lock(valkey, module="arena", action="login", identifier="Target@Test.example")
    seed_lock(valkey, module="arena", action="password_verify", identifier=target.id)
    seed_lock(valkey, module="arena", action="token_redeem", identifier=f"sub:{target.id}")
    seed_lock(valkey, module="arena", action="token_redeem", identifier=f"sub:{target.email_normalizado}")
    seed_lock(valkey, module="arena", action="login", ip=_IP)
    seed_lock(valkey, module="arena", action="login", identifier="bystander@test.example")
    seed_lock(valkey, module="web", action="login", identifier=target.email_normalizado)


async def _post_unlock(
    app: FastAPI, admin: ArenaUser, target: ArenaUser, *, password: str = _TEST_PASSWORD, source: str = "profile"
) -> Response:
    token = _login_token(app, admin)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        return await client.post(
            f"/admin/users/{target.id}/unlock",
            data={"confirm_password": password, "source": source},
            follow_redirects=False,
        )


async def _get_security_tab(app: FastAPI, admin: ArenaUser, target: ArenaUser) -> str:
    token = _login_token(app, admin)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get(f"/admin/users/{target.id}", params={"tab": "personal-security"})
    assert response.status_code == 200
    return response.text


@pytest.mark.asyncio
async def test_unlock_clears_every_account_bucket_and_leaves_the_rest(session: AsyncSession) -> None:
    app, valkey, admin, target = await _setup(session)
    _seed_target_locks(valkey, target)

    response = await _post_unlock(app, admin, target)

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/admin/users/{target.id}?tab=personal-security")
    assert not is_locked(valkey, module="arena", action="login", identifier="target@test.example")
    assert not is_locked(valkey, module="arena", action="password_verify", identifier=target.id)
    assert not is_locked(valkey, module="arena", action="token_redeem", identifier=f"sub:{target.id}")
    assert not is_locked(valkey, module="arena", action="token_redeem", identifier=f"sub:{target.email_normalizado}")
    assert is_locked(valkey, module="arena", action="login", ip=_IP), "the address is not this action's to lift"
    assert is_locked(valkey, module="arena", action="login", identifier="bystander@test.example")
    assert is_locked(valkey, module="web", action="login", identifier=target.email_normalizado), (
        "Arena admins stay in Arena"
    )

    audit = await admin_actions(session, "unlock_account")
    assert len(audit) == 1
    assert audit[0]["target_type"] == "arena_user"
    assert audit[0]["target_id"] == target.id
    assert audit[0]["detail"].startswith("keys_removed=8 fallback_removed=0 actions=")
    assert "arena/login" in audit[0]["detail"]
    assert "arena/token_redeem" in audit[0]["detail"]


@pytest.mark.asyncio
async def test_wrong_admin_password_changes_nothing(session: AsyncSession) -> None:
    app, valkey, admin, target = await _setup(session)
    _seed_target_locks(valkey, target)

    response = await _post_unlock(app, admin, target, password="not-the-password")

    assert response.status_code == 303
    assert is_locked(valkey, module="arena", action="login", identifier="target@test.example")
    assert await admin_actions(session, "unlock_account") == []


@pytest.mark.asyncio
async def test_list_source_redirects_to_the_user_list(session: AsyncSession) -> None:
    app, _valkey, admin, target = await _setup(session)

    response = await _post_unlock(app, admin, target, source="list")

    assert response.status_code == 303
    assert response.headers["location"].endswith("/admin/users")


@pytest.mark.asyncio
async def test_security_tab_shows_live_status_before_and_after(session: AsyncSession) -> None:
    app, valkey, admin, target = await _setup(session)
    seed_lock(valkey, module="arena", action="login", identifier=target.email_normalizado)

    before = await _get_security_tab(app, admin, target)
    assert "Locked" in before
    assert "lifts in 10 min" in before
    assert "confirmUnlockModal" in before

    await _post_unlock(app, admin, target)

    after = await _get_security_tab(app, admin, target)
    assert "Not locked" in after
    assert "lifts in" not in after


@pytest.mark.asyncio
async def test_unavailable_store_is_stated_and_audited_not_reported_as_done(session: AsyncSession) -> None:
    app, valkey, admin, target = await _setup(session)
    valkey.unavailable = True

    response = await _post_unlock(app, admin, target)

    assert response.status_code == 303
    audit = await admin_actions(session, "unlock_account")
    assert len(audit) == 1
    assert audit[0]["detail"] == "outcome=valkey_unavailable fallback_removed=0"

    page = await _get_security_tab(app, admin, target)
    assert "Status unknown" in page
    assert "Not locked" not in page
