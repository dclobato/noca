#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Throttling of the Arena secret-verification oracles and pending-flow writes.

Covers issue #144: ``POST /user/profile/2fa/confirm``, ``POST /user/profile/2fa/disable``,
``POST /auth/change-password`` (forced and voluntary), ``POST /auth/update-date-of-birth``
and ``POST /auth/accept-terms``. Arena route tests run without Valkey, so the shared
limiter answers from its process-local fallback, which ``tests/arena/conftest.py``
clears around every test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser
from arena.routes.auth_password import router as arena_auth_password_router
from arena.routes.auth_signup import router as arena_auth_signup_router
from arena.services.arena_auth_service import set_pending_password_change_token
from arena.services.token_service import ArenaTokenAction
from arena.services.user_2fa_service import Autenticacao2FA, TwoFASetupResult
from shared.db_schema import security_events
from tests.arena.test_arena_change_password import _create_user_requiring_pw_change
from tests.arena.test_arena_user_security_routes import _build_arena_app, _create_active_user, _login_token

_PASSWORD = "StrongPass1!"
_LIMIT = 2
_LOCKOUT = 900
_ENABLING = TwoFASetupResult(status=Autenticacao2FA.ENABLING, secret="JBSWY3DPEHPK3PXP")
_INVALID_CODE = TwoFASetupResult(status=Autenticacao2FA.INVALID_CODE)


def _configure(monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True, ip_max: int = 100) -> None:
    """Pin the shared auth-throttle knobs the routes read at request time."""
    target = "arena.routes.auth_common.settings"
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_ENABLED", enabled)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_WINDOW_SECONDS", 900)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES", _LIMIT)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_IP_MAX_FAILURES", ip_max)
    monkeypatch.setattr(f"{target}.AUTH_RATE_LIMIT_LOCKOUT_SECONDS", _LOCKOUT)


def _build_app(session: AsyncSession) -> FastAPI:
    """Extend the user-security test app with the password and signup routers."""
    app = _build_arena_app(session)
    app.include_router(arena_auth_password_router)
    app.include_router(arena_auth_signup_router)

    @app.post("/test/session/{key}")
    async def _session_writer(request: Request, key: str, value: str) -> HTMLResponse:
        request.session[key] = value
        return HTMLResponse("ok")

    @app.get("/test/session/{key}")
    async def _session_reader(request: Request, key: str) -> PlainTextResponse:
        return PlainTextResponse(str(request.session.get(key, "")))

    return app


def _client(app: FastAPI, *, ip: str = "203.0.113.10", token: str | None = None) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(app=app, client=(ip, 12345)), base_url="http://testserver")
    if token is not None:
        client.cookies.set("arena_access_token", token)
    return client


async def _event_count(session: AsyncSession, event_type: str) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(security_events).where(security_events.c.event_type == event_type)
        )
    ).scalar_one()


def _assert_locked(response: Response) -> None:
    assert response.status_code == 429
    assert _LOCKOUT - 5 <= int(response.headers["Retry-After"]) <= _LOCKOUT
    assert "text/html" in response.headers["content-type"]
    assert "Too many" in response.text


def _sent_emails(app: FastAPI) -> int:
    return int(app.state.email_service.send_email.call_count)


async def _disable(client: AsyncClient, password: str) -> Response:
    return await client.post("/user/profile/2fa/disable", data={"password": password}, follow_redirects=False)


async def _change(client: AsyncClient, current: str, new: str = "FreshNewPass1!") -> Response:
    return await client.post(
        "/auth/change-password",
        data={"current_password": current, "new_password": new, "confirm_password": new},
        follow_redirects=False,
    )


# ---------------------------------------------------------------------------
# password_verify bucket: 2FA disable + change-password
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_2fa_disable_locks_after_wrong_passwords_and_refuses_the_right_one(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    user = await _create_active_user(session, usa_2fa=True)
    app = _build_app(session)

    async with _client(app, token=_login_token(app, user)) as client:
        for _ in range(_LIMIT):
            assert (await _disable(client, "wrong")).status_code == 303
        _assert_locked(await _disable(client, "wrong"))
        _assert_locked(await _disable(client, _PASSWORD))

    await session.refresh(user)
    assert user.usa_2fa is True
    assert _sent_emails(app) == 0
    assert await _event_count(session, "auth_throttle_lockout") == 2
    assert await _event_count(session, "auth_failure") == _LIMIT


@pytest.mark.asyncio
async def test_2fa_disable_success_resets_the_counter(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    user = await _create_active_user(session, usa_2fa=True)
    app = _build_app(session)

    async with _client(app, token=_login_token(app, user)) as client:
        assert (await _disable(client, "wrong")).status_code == 303
        assert (await _disable(client, _PASSWORD)).status_code == 303
        await session.refresh(user)
        assert user.usa_2fa is False
        # The budget is whole again: two more wrong guesses are still allowed.
        for _ in range(_LIMIT):
            assert (await _disable(client, "wrong")).status_code == 303
        _assert_locked(await _disable(client, "wrong"))


@pytest.mark.asyncio
async def test_password_budget_is_shared_between_disable_and_change_password(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    user = await _create_active_user(session, usa_2fa=True)
    app = _build_app(session)

    async with _client(app, token=_login_token(app, user)) as client:
        assert (await _disable(client, "wrong")).status_code == 303
        assert (await _change(client, "wrong")).status_code == 303
        _assert_locked(await _disable(client, _PASSWORD))
        _assert_locked(await _change(client, _PASSWORD))

    await session.refresh(user)
    assert user.check_password(_PASSWORD)
    assert user.usa_2fa is True


@pytest.mark.asyncio
async def test_rotating_the_client_ip_does_not_bypass_the_user_bucket(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    user = await _create_active_user(session, usa_2fa=True)
    app = _build_app(session)
    token = _login_token(app, user)

    for index in range(_LIMIT):
        async with _client(app, ip=f"198.51.100.{index}", token=token) as client:
            assert (await _disable(client, "wrong")).status_code == 303
    async with _client(app, ip="198.51.100.200", token=token) as client:
        _assert_locked(await _disable(client, _PASSWORD))


@pytest.mark.asyncio
async def test_voluntary_change_password_locks_and_keeps_the_old_password(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    user = await _create_active_user(session)
    app = _build_app(session)

    async with _client(app, token=_login_token(app, user)) as client:
        for _ in range(_LIMIT):
            assert (await _change(client, "wrong")).status_code == 303
        _assert_locked(await _change(client, _PASSWORD))

    await session.refresh(user)
    assert user.check_password(_PASSWORD)
    assert _sent_emails(app) == 0


@pytest.mark.asyncio
async def test_voluntary_change_password_resets_once_the_current_password_verifies(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    user = await _create_active_user(session)
    app = _build_app(session)

    async with _client(app, token=_login_token(app, user)) as client:
        assert (await _change(client, "wrong")).status_code == 303
        # Right password, rejected new one: the secret verified, so the count resets.
        assert (await _change(client, _PASSWORD, new="short")).status_code == 303
        for _ in range(_LIMIT):
            assert (await _change(client, "wrong")).status_code == 303
        _assert_locked(await _change(client, "wrong"))


@pytest.mark.asyncio
async def test_forced_change_password_with_pending_token_locks(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    user = await _create_user_requiring_pw_change(session)
    await session.commit()
    app = _build_app(session)
    pending = set_pending_password_change_token(user, app.state.jwt_service, next_page=None)

    async with _client(app) as client:
        await client.post("/test/session/pending_pw_change_token", params={"value": pending})
        for _ in range(_LIMIT):
            assert (await _change(client, "wrong")).status_code == 303
        _assert_locked(await _change(client, "OldPass1!"))

    await session.refresh(user)
    assert user.check_password("OldPass1!")
    assert user.precisa_trocar_senha is True


# ---------------------------------------------------------------------------
# 2fa_confirm bucket
# ---------------------------------------------------------------------------


class _PlainSecretsManager:
    """Identity stand-in for the ``EncryptedString`` SecretsManager.

    Arena route tests never initialise the real manager; seeding a tentative
    TOTP secret needs one, so this fake stores the plaintext as-is.
    """

    def encrypt(self, plaintext: bytes) -> tuple[str, bytes]:
        return "plain", plaintext

    def decrypt(self, ciphertext: bytes, version_hint: str | None) -> tuple[str, bytes]:
        return "plain", ciphertext


def _confirm_patches(confirm: Any) -> Any:
    return (
        patch("arena.routes.user_security.user_2fa_service.validar_token_ativacao_2fa", return_value=_ENABLING),
        patch("arena.routes.user_security.user_2fa_service.confirmar_ativacao_2fa", new=confirm),
    )


async def _confirm(client: AsyncClient, code: str = "000000") -> Response:
    return await client.post("/user/profile/2fa/confirm", data={"full_code": code}, follow_redirects=False)


@pytest.mark.asyncio
async def test_2fa_confirm_lockout_voids_the_pending_secret(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr("shared.db_schema.custom_types._secrets_manager", _PlainSecretsManager())
    user = await _create_active_user(session)
    user.otp_secret = "JBSWY3DPEHPK3PXP"
    await session.commit()
    await session.refresh(user)
    assert user.otp_secret == "JBSWY3DPEHPK3PXP"
    app = _build_app(session)
    token_patch, confirm_patch = _confirm_patches(AsyncMock(return_value=_INVALID_CODE))

    with token_patch, confirm_patch:
        async with _client(app, token=_login_token(app, user)) as client:
            await client.post("/test/session/activating_2fa_token", params={"value": "setup-token"})
            for _ in range(_LIMIT - 1):
                assert (await _confirm(client)).status_code == 303
            _assert_locked(await _confirm(client))
            assert (await client.get("/test/session/activating_2fa_token")).text == ""

    await session.refresh(user)
    assert user.otp_secret is None
    assert user.usa_2fa is False
    assert _sent_emails(app) == 0
    assert await _event_count(session, "auth_failure") == _LIMIT


@pytest.mark.asyncio
async def test_2fa_confirm_refuses_a_correct_code_while_locked(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch)
    user = await _create_active_user(session)
    app = _build_app(session)
    token = _login_token(app, user)
    token_patch, confirm_patch = _confirm_patches(AsyncMock(return_value=_INVALID_CODE))
    with token_patch, confirm_patch:
        async with _client(app, token=token) as client:
            for _ in range(_LIMIT):
                await _confirm(client)

    token_patch, confirm_patch = _confirm_patches(AsyncMock(side_effect=AssertionError("verified while locked")))
    with token_patch, confirm_patch:
        async with _client(app, token=token) as client:
            _assert_locked(await _confirm(client, "123456"))

    await session.refresh(user)
    assert user.usa_2fa is False
    assert await _event_count(session, "auth_throttle_lockout") == 1


# ---------------------------------------------------------------------------
# Pending-flow writes: IP-only quota
# ---------------------------------------------------------------------------


async def _dob(client: AsyncClient, value: str = "not-a-date") -> Response:
    return await client.post("/auth/update-date-of-birth", data={"date_of_birth": value}, follow_redirects=False)


async def _terms(client: AsyncClient, *, accept: bool = False) -> Response:
    data = {"terms": "on"} if accept else {}
    return await client.post("/auth/accept-terms", data=data, follow_redirects=False)


@pytest.mark.asyncio
async def test_accept_terms_counts_every_pending_attempt_and_writes_nothing_while_locked(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, ip_max=_LIMIT)
    user = await _create_active_user(session)
    accepted_before = user.aceitou_termos_privacidade
    app = _build_app(session)

    async with _client(app) as client:
        await client.post("/test/session/pending_tos_uid", params={"value": user.id})
        for _ in range(_LIMIT):
            assert (await _terms(client)).status_code == 200
        _assert_locked(await _terms(client, accept=True))

    await session.refresh(user)
    assert user.aceitou_termos_privacidade == accepted_before
    assert await _event_count(session, "auth_throttle_lockout") == 1


@pytest.mark.asyncio
async def test_update_date_of_birth_locks_after_the_window(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, ip_max=_LIMIT)
    user = await _create_active_user(session)
    app = _build_app(session)

    async with _client(app) as client:
        await client.post("/test/session/pending_age_uid", params={"value": user.id})
        for _ in range(_LIMIT):
            assert (await _dob(client)).status_code == 303
        _assert_locked(await _dob(client, "2000-01-01"))


@pytest.mark.asyncio
async def test_pending_flows_without_a_pending_uid_do_not_consume_the_window(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(monkeypatch, ip_max=_LIMIT)
    user = await _create_active_user(session)
    app = _build_app(session)

    async with _client(app) as client:
        for _ in range(_LIMIT + 1):
            assert (await _dob(client)).status_code == 303
            assert (await _terms(client)).status_code == 303
        await client.post("/test/session/pending_age_uid", params={"value": user.id})
        for _ in range(_LIMIT):
            assert (await _dob(client)).status_code == 303
        _assert_locked(await _dob(client))


@pytest.mark.asyncio
async def test_pending_flows_use_independent_buckets(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, ip_max=_LIMIT)
    user = await _create_active_user(session)
    app = _build_app(session)

    async with _client(app) as client:
        await client.post("/test/session/pending_tos_uid", params={"value": user.id})
        await client.post("/test/session/pending_age_uid", params={"value": user.id})
        for _ in range(_LIMIT):
            assert (await _terms(client)).status_code == 200
        _assert_locked(await _terms(client))
        assert (await _dob(client)).status_code == 303


@pytest.mark.asyncio
async def test_disabled_switch_turns_every_cap_off(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, enabled=False, ip_max=_LIMIT)
    user = await _create_active_user(session, usa_2fa=True)
    app = _build_app(session)

    async with _client(app, token=_login_token(app, user)) as client:
        await client.post("/test/session/pending_age_uid", params={"value": user.id})
        for _ in range(_LIMIT * 3):
            assert (await _disable(client, "wrong")).status_code == 303
            assert (await _dob(client)).status_code == 303


# ---------------------------------------------------------------------------
# The reset-link GET is not a free token oracle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_link_get_does_not_judge_the_token(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening a tampered reset link renders the form instead of grading the token.

    The GET is anonymous and unthrottled, so validating there answered "is this
    token valid, and expired or forged?" for free while the POST performed the
    same check behind the ``password-reset`` budget. The GET now judges nothing;
    the POST stays the single verdict point.
    """
    _configure(monkeypatch)
    await _create_active_user(session)
    app = _build_app(session)

    async with _client(app) as client:
        for _ in range(_LIMIT * 5):
            response = await client.get(
                "/auth/password-reset", params={"token": "not-a-real-token"}, follow_redirects=False
            )
            assert response.status_code == 200, "a bad token must not redirect: that is the oracle"
            assert "login" not in response.headers.get("location", "")


@pytest.mark.asyncio
async def test_reset_submit_still_counts_a_bad_token(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """The POST remains throttled, so the flow as a whole is still capped."""
    _configure(monkeypatch)
    await _create_active_user(session)
    app = _build_app(session)
    payload = {"token": "not-a-real-token", "password": "FreshNewPass1!", "confirm_password": "FreshNewPass1!"}

    async with _client(app) as client:
        for _ in range(_LIMIT):
            assert (await client.post("/auth/password-reset", data=payload, follow_redirects=False)).status_code == 303
        _assert_locked(await client.post("/auth/password-reset", data=payload, follow_redirects=False))


# ---------------------------------------------------------------------------
# token_redeem: the activation and consent links redeem on the GET
# ---------------------------------------------------------------------------


def _redeem_token(app: FastAPI, user: ArenaUser, action: ArenaTokenAction) -> str:
    """Mint a real redemption JWT for one of the two link routes."""
    return str(app.state.jwt_service.criar(action=action, sub=user.email_normalizado, expires_in=3600))


@pytest.mark.asyncio
async def test_a_bad_activation_token_is_counted_and_then_locked(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The GET performs the action, so it cannot defer the verdict -- it counts it."""
    _configure(monkeypatch)
    app = _build_app(session)

    async with _client(app) as client:
        for _ in range(_LIMIT):
            bad = await client.get("/auth/activate", params={"token": "not-a-token"}, follow_redirects=False)
            assert bad.status_code == 303
        _assert_locked(await client.get("/auth/activate", params={"token": "not-a-token"}, follow_redirects=False))


@pytest.mark.asyncio
async def test_clicking_a_working_activation_link_twice_costs_nothing(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful redemption and a repeat click must not spend the budget.

    A mail client that prefetches the link, or a user who opens it twice, would
    otherwise lock themselves out of a link that worked.
    """
    _configure(monkeypatch)
    user = await _create_active_user(session, email="activate-twice@test.example")
    user.email_confirmado = False
    await session.commit()
    app = _build_app(session)
    token = _redeem_token(app, user, ArenaTokenAction.VALIDATE_EMAIL)

    async with _client(app) as client:
        first = await client.get("/auth/activate", params={"token": token}, follow_redirects=False)
        assert first.status_code == 303
        await session.refresh(user)
        assert user.email_confirmado, "the first click must really redeem the token"

        # Every later click answers ALREADY_CONFIRMED, which is not a failure.
        for _ in range(_LIMIT * 3):
            repeat = await client.get("/auth/activate", params={"token": token}, follow_redirects=False)
            assert repeat.status_code == 303, "a working link must never lock its own user out"


@pytest.mark.asyncio
async def test_a_success_clears_the_redeem_counter(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failures before a successful redemption do not carry past it."""
    _configure(monkeypatch)
    user = await _create_active_user(session, email="redeem-reset@test.example")
    user.email_confirmado = False
    await session.commit()
    app = _build_app(session)
    token = _redeem_token(app, user, ArenaTokenAction.VALIDATE_EMAIL)

    async with _client(app) as client:
        # One failure short of the cap, on this token's own bucket.
        spent = await client.get("/auth/activate", params={"token": token[:-1]}, follow_redirects=False)
        assert spent.status_code == 303
        ok = await client.get("/auth/activate", params={"token": token}, follow_redirects=False)
        assert ok.status_code == 303
        await session.refresh(user)
        assert user.email_confirmado
        for _ in range(_LIMIT):
            again = await client.get("/auth/activate", params={"token": token}, follow_redirects=False)
            assert again.status_code == 303


@pytest.mark.asyncio
async def test_tampering_one_users_link_accumulates_against_that_user(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distinct tamperings of one link share a bucket: the identifier is the unverified ``sub``."""
    _configure(monkeypatch)
    user = await _create_active_user(session, email="tamper-sub@test.example")
    user.email_confirmado = False
    await session.commit()
    app = _build_app(session)
    token = _redeem_token(app, user, ArenaTokenAction.VALIDATE_EMAIL)

    async with _client(app) as client:
        for cut in range(1, _LIMIT + 1):
            bad = await client.get("/auth/activate", params={"token": token[:-cut]}, follow_redirects=False)
            assert bad.status_code == 303
        _assert_locked(await client.get("/auth/activate", params={"token": token[:-9]}, follow_redirects=False))


@pytest.mark.asyncio
async def test_a_locked_bucket_still_redeems_the_genuine_link(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock refuses bad tokens only, so a stranger cannot hold an activation hostage."""
    _configure(monkeypatch)
    user = await _create_active_user(session, email="hostage@test.example")
    user.email_confirmado = False
    await session.commit()
    app = _build_app(session)
    token = _redeem_token(app, user, ArenaTokenAction.VALIDATE_EMAIL)

    async with _client(app) as client:
        for _ in range(_LIMIT):
            await client.get("/auth/activate", params={"token": token[:-1]}, follow_redirects=False)
        _assert_locked(await client.get("/auth/activate", params={"token": token[:-1]}, follow_redirects=False))

        genuine = await client.get("/auth/activate", params={"token": token}, follow_redirects=False)
        assert genuine.status_code == 303
        await session.refresh(user)
        assert user.email_confirmado, "a locked bucket must not refuse the real link"


@pytest.mark.asyncio
async def test_a_success_does_not_clear_the_ip_counter(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """One's own working link must not wipe the guessing budget of one's IP."""
    _configure(monkeypatch, ip_max=3)
    mine = await _create_active_user(session, email="mine@test.example")
    mine.email_confirmado = False
    other = await _create_active_user(session, email="other@test.example")
    other.email_confirmado = False
    await session.commit()
    app = _build_app(session)
    my_token = _redeem_token(app, mine, ArenaTokenAction.VALIDATE_EMAIL)
    their_token = _redeem_token(app, other, ArenaTokenAction.VALIDATE_EMAIL)

    async with _client(app) as client:
        # IP failure 1, then a success that clears only my own bucket.
        assert (
            await client.get("/auth/activate", params={"token": my_token[:-1]}, follow_redirects=False)
        ).status_code == 303
        assert (
            await client.get("/auth/activate", params={"token": my_token}, follow_redirects=False)
        ).status_code == 303
        # IP failures 2 and 3, each under the per-account cap.
        assert (
            await client.get("/auth/activate", params={"token": their_token[:-1]}, follow_redirects=False)
        ).status_code == 303
        assert (
            await client.get("/auth/activate", params={"token": "garbage"}, follow_redirects=False)
        ).status_code == 303
        _assert_locked(await client.get("/auth/activate", params={"token": "garbage-2"}, follow_redirects=False))


# The consent-link throttle test moved to ``test_parental_consent_grant.py`` with the
# grant mutation: the consent GET no longer redeems (or counts), the POST does both.


def test_token_redeem_identifier_prefers_the_unverified_sub() -> None:
    """A decodable payload names the account; anything else falls back to the token itself."""
    import base64
    import json

    from arena.routes.auth_token_redeem import token_redeem_identifier

    payload = base64.urlsafe_b64encode(json.dumps({"sub": "someone@test.example"}).encode()).rstrip(b"=").decode()
    assert token_redeem_identifier(f"h.{payload}.sig") == "sub:someone@test.example"
    assert token_redeem_identifier(f"h.{payload}.tampered") == "sub:someone@test.example"
    assert token_redeem_identifier("not-a-token") == "token:not-a-token"
    assert token_redeem_identifier("a.!!!.c") == "token:a.!!!.c"
    empty = base64.urlsafe_b64encode(b'{"sub": ""}').rstrip(b"=").decode()
    assert token_redeem_identifier(f"h.{empty}.c") == f"token:h.{empty}.c"
