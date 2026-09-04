#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the UberAdmin lockouts page (``/uberadmin/lockouts``).

The decisions under test:

- an IP unlock lifts the ``web`` and ``animator`` buckets of that address
  and never an ``arena`` one
- a login unlock states its scope: *All contests* clears the bare-name
  ``login`` bucket, the UberAdmin's own, and every contest's scoped
  ``contest-login`` bucket, while a single contest clears only that one
  (the scoped cases live in ``test_uberadmin_lockout_scope.py``)
- the reconfirmation budget applies: a wrong password changes nothing, and
  a locked UberAdmin gets the shared ``429`` page
- every outcome is audited as an ``admin_action`` row in the Web module
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import security_events
from shared.enumerations import RoleEnum
from shared.services.auth_rate_limit import hash_identifier
from tests.conftest import _make_user
from tests.shared._auth_fake_valkey import AuthFakeValkey
from tests.web.test_inactive_contest_routes import _build_app, _login_uberadmin
from web.config import settings
from web.models.contest import Contest
from web.models.users import UberAdmin

_IP = "203.0.113.9"
_PAGE = "/uberadmin/lockouts"
_PASSWORD = "TestPass1!"


def _lock_key(*, module: str, action: str, identifier: str | None = None, ip: str | None = None) -> str:
    if identifier is not None:
        digest = hash_identifier(identifier, secret=settings.JWT_SECRET_KEY)
        return f"auth:rate-limit:{module}:{action}:acct:{digest}:lock"
    return f"auth:rate-limit:{module}:{action}:ip:{ip}:lock"


def _seed(valkey: AuthFakeValkey, **bucket: Any) -> None:
    key = _lock_key(**bucket)
    valkey.locks[key] = valkey.clock + 600
    valkey.counts[key.removesuffix(":lock") + ":failures"] = (5, valkey.clock + 900)


def _locked(valkey: AuthFakeValkey, **bucket: Any) -> bool:
    return _lock_key(**bucket) in valkey.live_keys()


def _second_contest(session: AsyncSession, uberadmin: UberAdmin) -> Contest:
    """A second active contest, so a login can exist in two of them at once."""
    contest = Contest(
        contest_name="Other Contest",
        contest_url="http://other.example.com",
        login_slug="other-contest",
        start_time=datetime.now(UTC) - timedelta(minutes=30),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    return contest


async def _audit(session: AsyncSession, action: str) -> list[dict[str, Any]]:
    result = await session.execute(
        select(security_events.c.metadata)
        .where(security_events.c.module == "web", security_events.c.event_type == "admin_action")
        .order_by(security_events.c.id)
    )
    return [dict(row or {}) for row in result.scalars() if (row or {}).get("action") == action]


async def _setup(session: AsyncSession, uberadmin: UberAdmin) -> tuple[FastAPI, AuthFakeValkey, str]:
    await session.commit()
    app, auth_service = _build_app(session)
    valkey = AuthFakeValkey()
    app.state.valkey_runtime = valkey
    token = await _login_uberadmin(auth_service, session, uberadmin.username)
    return app, valkey, token


def _client(app: FastAPI, token: str) -> AsyncClient:
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    client.cookies.set("noca_access_token", token)
    return client


async def _post(app: FastAPI, token: str, path: str, *, password: str = _PASSWORD, **data: str) -> Response:
    async with _client(app, token) as client:
        return await client.post(path, data={"password": password, **data}, follow_redirects=False)


@pytest.mark.asyncio
async def test_page_renders_the_status_of_a_prefilled_address(session: AsyncSession, uberadmin: UberAdmin) -> None:
    app, valkey, token = await _setup(session, uberadmin)
    _seed(valkey, module="animator", action="control", ip=_IP)

    async with _client(app, token) as client:
        plain = await client.get(_PAGE)
        prefilled = await client.get(_PAGE, params={"ip": _IP})
        malformed = await client.get(_PAGE, params={"ip": "unknown"})

    assert plain.status_code == 200
    assert "Status for" not in plain.text
    assert "animator/control" in prefilled.text
    assert "lifts in 10 min" in prefilled.text
    assert "Enter one IPv4 or IPv6 address." in malformed.text


@pytest.mark.asyncio
async def test_unlock_ip_lifts_web_and_animator_but_never_arena(session: AsyncSession, uberadmin: UberAdmin) -> None:
    app, valkey, token = await _setup(session, uberadmin)
    _seed(valkey, module="web", action="login", ip=_IP)
    _seed(valkey, module="web", action="contest-login", ip=_IP)
    _seed(valkey, module="animator", action="control", ip=_IP)
    _seed(valkey, module="arena", action="login", ip=_IP)

    response = await _post(app, token, f"{_PAGE}/unlock-ip", ip=_IP)

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"{_PAGE}?ip={_IP}")
    assert not _locked(valkey, module="web", action="login", ip=_IP)
    assert not _locked(valkey, module="web", action="contest-login", ip=_IP)
    assert not _locked(valkey, module="animator", action="control", ip=_IP)
    assert _locked(valkey, module="arena", action="login", ip=_IP), "Arena buckets are not the UberAdmin's to lift"
    audit = await _audit(session, "unlock_ip")
    assert len(audit) == 1
    assert (audit[0]["target_type"], audit[0]["target_id"]) == ("client_ip", _IP)
    assert "actions=animator/control,web/contest-login,web/login" in audit[0]["detail"]


@pytest.mark.asyncio
async def test_unlock_login_over_all_contests_covers_every_scoped_bucket(
    session: AsyncSession, uberadmin: UberAdmin, running_contest: Contest
) -> None:
    """*All contests* is the deliberate wide unlock: every contest's scoped bucket, plus the global ones.

    ``contest-login`` is keyed on ``{contest_id}:{username}``, so the wide
    unlock has to name one bucket per contest carrying the login. It also
    covers the bare-name ``login`` bucket and the UberAdmin's own, which a
    single-contest scope deliberately does not -- an UberAdmin is not a contest.
    """
    other = _second_contest(session, uberadmin)
    await session.flush()
    team_a = _make_user(session, running_contest, uberadmin, "team042", "Team 42", RoleEnum.TEAM)
    team_b = _make_user(session, other, uberadmin, "team042", "Team 42 again", RoleEnum.TEAM)
    await session.flush()
    app, valkey, token = await _setup(session, uberadmin)
    _seed(valkey, module="web", action="contest-login", identifier=f"{running_contest.id}:Team042")
    _seed(valkey, module="web", action="contest-login", identifier=f"{other.id}:team042")
    _seed(valkey, module="web", action="login", identifier="team042")
    _seed(valkey, module="web", action="password-confirm", identifier=f"user:{team_a.id}")
    _seed(valkey, module="web", action="password-confirm", identifier=f"user:{team_b.id}")
    # Another UberAdmin's reconfirmation bucket, not the acting one's: locking that would refuse the test itself.
    _seed(valkey, module="web", action="password-confirm", identifier="uberadmin:someone-else")
    _seed(valkey, module="web", action="contest-login", identifier=f"{running_contest.id}:team043")

    response = await _post(app, token, f"{_PAGE}/unlock-account", identifier="team042", contest_scope="all")

    assert response.status_code == 303
    assert not _locked(valkey, module="web", action="contest-login", identifier=f"{running_contest.id}:team042")
    assert not _locked(valkey, module="web", action="contest-login", identifier=f"{other.id}:team042")
    assert not _locked(valkey, module="web", action="login", identifier="team042")
    assert not _locked(valkey, module="web", action="password-confirm", identifier=f"user:{team_a.id}")
    assert not _locked(valkey, module="web", action="password-confirm", identifier=f"user:{team_b.id}")
    assert _locked(valkey, module="web", action="password-confirm", identifier="uberadmin:someone-else")
    assert _locked(valkey, module="web", action="contest-login", identifier=f"{running_contest.id}:team043")
    audit = await _audit(session, "unlock_account")
    assert (audit[0]["target_type"], audit[0]["target_id"]) == (
        "login",
        "team042 in all contests (2 contest users)",
    )


@pytest.mark.asyncio
async def test_unlock_login_honours_an_explicit_event_hash(session: AsyncSession, uberadmin: UberAdmin) -> None:
    app, valkey, token = await _setup(session, uberadmin)
    _seed(valkey, module="web", action="login", identifier="ghost")
    digest = hash_identifier("ghost", secret=settings.JWT_SECRET_KEY)

    response = await _post(app, token, f"{_PAGE}/unlock-account", identifier_hash=digest)

    assert response.status_code == 303
    assert not _locked(valkey, module="web", action="login", identifier="ghost")
    audit = await _audit(session, "unlock_account")
    assert (audit[0]["target_type"], audit[0]["target_id"]) == ("identifier_hash", digest[:12])


@pytest.mark.asyncio
async def test_wrong_password_changes_nothing_and_a_locked_uberadmin_gets_429(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    app, valkey, token = await _setup(session, uberadmin)
    _seed(valkey, module="web", action="login", ip=_IP)

    for _ in range(settings.AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES):
        refused = await _post(app, token, f"{_PAGE}/unlock-ip", ip=_IP, password="wrong")
        assert refused.status_code == 303
    locked = await _post(app, token, f"{_PAGE}/unlock-ip", ip=_IP)

    assert locked.status_code == 429
    assert "Back to sign-in lockouts" in locked.text
    assert _locked(valkey, module="web", action="login", ip=_IP)
    assert await _audit(session, "unlock_ip") == []


@pytest.mark.asyncio
async def test_dashboard_card_and_event_rows_link_to_the_page(session: AsyncSession, uberadmin: UberAdmin) -> None:
    from shared.services.security_events import record_security_event

    digest = hash_identifier("team042", secret=settings.JWT_SECRET_KEY)
    await record_security_event(
        session,
        module="web",
        event_type="auth_throttle_lockout",
        actor_label="team042",
        identifier_hash=digest,
        client_ip=_IP,
    )
    app, _valkey, token = await _setup(session, uberadmin)

    async with _client(app, token) as client:
        dashboard = await client.get("/uberadmin/")
        events = await client.get("/uberadmin/security-events")

    assert dashboard.status_code == 200
    assert "/uberadmin/lockouts" in dashboard.text
    assert events.status_code == 200
    assert f"/uberadmin/lockouts?ip={_IP}" in events.text
    assert f"identifier=team042&amp;identifier_hash={digest}" in events.text


@pytest.mark.asyncio
async def test_a_refused_reconfirmation_carries_the_typed_subject_back(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    """A mistyped password must not cost the operator the subject they typed.

    Both refusals go back to the page, so both have to carry the prefill: the
    redirect after a wrong password, and the ``429`` page's own back link once
    the reconfirmation budget is spent.
    """
    app, valkey, token = await _setup(session, uberadmin)
    _seed(valkey, module="web", action="login", ip=_IP)

    wrong = await _post(app, token, f"{_PAGE}/unlock-ip", ip=_IP, password="wrong")
    assert wrong.status_code == 303
    assert f"ip={_IP}" in wrong.headers["location"]

    for _ in range(settings.AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES):
        await _post(app, token, f"{_PAGE}/unlock-account", identifier="team042", password="wrong")
    locked = await _post(app, token, f"{_PAGE}/unlock-account", identifier="team042")

    assert locked.status_code == 429
    assert "identifier=team042" in locked.text


@pytest.mark.asyncio
async def test_the_page_survives_a_hostile_prefill(session: AsyncSession, uberadmin: UberAdmin) -> None:
    """Query values are operator-supplied and unbounded; the page must not be.

    A 300-character login, an address that is not one, and non-Latin text all
    reach the status heading, which is why it wraps rather than widening the
    card, and why every value is escaped rather than rendered as markup.
    """
    app, _valkey, token = await _setup(session, uberadmin)
    long_login = "l" * 300
    async with _client(app, token) as client:
        overlong = await client.get(_PAGE, params={"identifier": long_login})
        unicode_ip = await client.get(_PAGE, params={"ip": "２０３.０.１１３.９ 🔒"})
        injected = await client.get(_PAGE, params={"identifier": "<script>alert(1)</script>"})

    for response in (overlong, unicode_ip, injected):
        assert response.status_code == 200
        assert 'id="lockout-status-heading"' in response.text
        assert "text-break" in response.text
    assert long_login in overlong.text
    assert "Enter one IPv4 or IPv6 address." in unicode_ip.text
    assert "<script>alert(1)</script>" not in injected.text
    assert "&lt;script&gt;" in injected.text
