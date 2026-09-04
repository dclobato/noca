#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The per-IP bucket at the login 2FA step locks on distinct accounts, not raw failures.

Reaching ``POST /auth/2fa`` requires a valid password, so the account bucket
already bounds an attacker holding one credential. What the IP bucket is left to
catch is spraying -- failures spanning *many* accounts from one host -- and that
is the only shape it now locks on. One person fumbling their own codes from a
school lab's shared NAT address no longer costs their classmates the 2FA step.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request
from fastapi.responses import HTMLResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser
from arena.services.arena_auth_service import set_pending_2fa_token
from arena.services.user_2fa_service import Autenticacao2FA, TwoFAValidationResult
from shared.enumerations import ArenaRole
from tests.arena.test_arena_2fa_login import _build_arena_app

_FAILED = TwoFAValidationResult(
    success=False,
    method_used=Autenticacao2FA.INVALID_CODE,
    error_message="Invalid 2FA code.",
)


async def _create_2fa_user(session: AsyncSession, email: str) -> ArenaUser:
    """Persist one active Arena user with 2FA enabled."""
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome="2FA User",
        email_normalizado=email,
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        aceitou_termos_privacidade=True,
        com_foto=False,
        usa_2fa=True,
        precisa_trocar_senha=False,
        session_version=1,
        affiliation_id="test-affiliation",
        preferred_language_id="python",
        country_code="BR",
        prefered_language="en-US",
    )
    user.password = "StrongPass1!"
    session.add(user)
    await session.flush()
    return user


def _configure(monkeypatch: pytest.MonkeyPatch, *, distinct: int, ip_max: int, account_max: int = 100) -> None:
    monkeypatch.setattr("arena.routes.auth_common.settings.AUTH_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr("arena.routes.auth_common.settings.AUTH_RATE_LIMIT_IP_MAX_FAILURES", ip_max)
    monkeypatch.setattr("arena.routes.auth_common.settings.AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES", account_max)
    monkeypatch.setattr("arena.routes.auth_2fa.settings.AUTH_RATE_LIMIT_2FA_IP_DISTINCT_ACCOUNTS", distinct)


def _build_app(session: AsyncSession) -> Any:
    """The 2FA test app plus a stub that plants a pending-2FA token in the session."""
    app = _build_arena_app(session)

    @app.post("/test-plant-2fa-token")
    async def _plant(request: Request) -> HTMLResponse:
        request.session["pending_2fa_token"] = request.query_params["token"]
        return HTMLResponse("ok")

    # A lockout re-renders the real two-factor template, whose footer resolves
    # the legal routes; the login-flow app does not mount them.
    for path, name in (("/legal/terms", "arena_terms_of_service"), ("/legal/privacy", "arena_privacy_policy")):

        @app.get(path, name=name)
        async def _legal() -> HTMLResponse:
            return HTMLResponse("legal")

    return app


async def _post_wrong_code(app: Any, user: ArenaUser, *, client_ip: str = "203.0.113.7") -> Any:
    """Submit one wrong 2FA code for *user* from *client_ip*.

    Each attempt uses a fresh client and plants its own pending-2FA token, which
    is what a real retry looks like: the token survives a failed submission, so
    the browser comes back to the same step.
    """
    token = set_pending_2fa_token(user, app.state.jwt_service, remember_me=False)
    transport = ASGITransport(app=app, client=(client_ip, 44321))
    with patch(
        "arena.routes.auth_2fa.user_2fa_service.validar_codigo_2fa",
        new=AsyncMock(return_value=_FAILED),
    ):
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            await client.post("/test-plant-2fa-token", params={"token": token})
            return await client.post("/auth/2fa", data={"full_code": "000000"}, follow_redirects=False)


@pytest.mark.asyncio
async def test_one_account_fumbling_never_locks_the_address(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The DoS this closes: 20 honest failures on one account leave the address usable."""
    _configure(monkeypatch, distinct=3, ip_max=3)
    app = _build_app(session)
    user = await _create_2fa_user(session, "solo@test.example")
    await session.commit()

    for _ in range(20):
        response = await _post_wrong_code(app, user)
        assert response.status_code == 303, "a single account's failures must never trip the IP bucket"


@pytest.mark.asyncio
async def test_failures_across_distinct_accounts_still_lock_the_address(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The spray defence survives: failures spanning enough accounts still lock the IP."""
    _configure(monkeypatch, distinct=3, ip_max=3)
    app = _build_app(session)
    users = [await _create_2fa_user(session, f"spray{index}@test.example") for index in range(3)]
    await session.commit()

    # Each attempt is judged before its own failure is counted, so the lock the
    # third failure creates is what the fourth attempt meets.
    for user in users:
        assert (await _post_wrong_code(app, user)).status_code == 303

    # A fourth, untouched account is refused from the same address: the lock is
    # on the address, which is what makes it a spray defence.
    fourth = await _create_2fa_user(session, "spray-late@test.example")
    await session.commit()
    locked = await _post_wrong_code(app, fourth)

    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_other_addresses_are_unaffected(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """A locked address does not lock the account for someone else's address."""
    _configure(monkeypatch, distinct=2, ip_max=2)
    app = _build_app(session)
    first = await _create_2fa_user(session, "one@test.example")
    second = await _create_2fa_user(session, "two@test.example")
    await session.commit()

    assert (await _post_wrong_code(app, first, client_ip="198.51.100.1")).status_code == 303
    assert (await _post_wrong_code(app, second, client_ip="198.51.100.1")).status_code == 303
    assert (await _post_wrong_code(app, first, client_ip="198.51.100.1")).status_code == 429
    assert (await _post_wrong_code(app, first, client_ip="198.51.100.2")).status_code == 303


@pytest.mark.asyncio
async def test_account_bucket_still_stops_the_credential_holder(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real cap at this step is unchanged: the account bucket still locks."""
    _configure(monkeypatch, distinct=3, ip_max=100, account_max=3)
    app = _build_app(session)
    user = await _create_2fa_user(session, "capped@test.example")
    await session.commit()

    for _ in range(3):
        assert (await _post_wrong_code(app, user)).status_code == 303

    assert (await _post_wrong_code(app, user)).status_code == 429


@pytest.mark.asyncio
async def test_threshold_of_one_restores_the_plain_ip_count(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operators keep an escape hatch back to the previous behaviour."""
    _configure(monkeypatch, distinct=1, ip_max=2)
    app = _build_app(session)
    user = await _create_2fa_user(session, "plain@test.example")
    await session.commit()

    assert (await _post_wrong_code(app, user)).status_code == 303
    assert (await _post_wrong_code(app, user)).status_code == 303
    assert (await _post_wrong_code(app, user)).status_code == 429
