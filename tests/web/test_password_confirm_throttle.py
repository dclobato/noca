#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The shared password-reconfirmation budget behind five Web routes.

Contract under test (issue #145): a wrong password is counted and recorded;
past the cap the actor is refused with the shared ``429`` page *before* the
password is checked, so the correct password no longer works and no protected
work starts; the five routes draw on one budget; a match resets it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import security_events
from tests.web._contest_admin_test_support import actor_token, admin_on, build_contest_admin_app
from tests.web.test_inactive_contest_routes import _build_app as build_uberadmin_app
from tests.web.test_inactive_contest_routes import _login_uberadmin
from tests.web.test_profile_route import _build_profile_app, _uberadmin_token
from web.config import settings
from web.models.contest import Contest
from web.models.users import UberAdmin
from web.routes.contest_admin import router as contest_admin_router
from web.services.password_confirm_throttle import PASSWORD_CONFIRM_LIMITER

pytestmark = pytest.mark.asyncio

GOOD = "TestPass1!"
IP_A = ("203.0.113.10", 12345)
IP_B = ("198.51.100.7", 12345)


def _client(app, *, ip: tuple[str, int] = IP_A) -> AsyncClient:  # type: ignore[no-untyped-def]
    return AsyncClient(transport=ASGITransport(app=app, client=ip), base_url="http://testserver")


def _cap(monkeypatch: pytest.MonkeyPatch, *, account: int = 2, ip: int = 20, enabled: bool = True) -> None:
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_ENABLED", enabled)
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES", account)
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_IP_MAX_FAILURES", ip)
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_WINDOW_SECONDS", 900)
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_LOCKOUT_SECONDS", 900)


async def _events(session: AsyncSession, event_type: str) -> list[dict[str, object]]:
    rows = (
        await session.execute(
            select(security_events.c.severity, security_events.c.actor_label, security_events.c["metadata"]).where(
                security_events.c.event_type == event_type
            )
        )
    ).all()
    return [{"severity": r[0], "actor_label": r[1], "metadata": r[2]} for r in rows]


def _is_lockout(response) -> bool:  # type: ignore[no-untyped-def]
    return (
        response.status_code == 429
        and "Retry-After" in response.headers
        and "Too many failed password confirmations" in response.text
    )


# ---------------------------------------------------------------------------
# /profile/password
# ---------------------------------------------------------------------------


async def test_profile_password_locks_after_the_cap_and_refuses_the_correct_password(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cap(monkeypatch, account=2)
    app, auth_service = _build_profile_app(session)
    token = await _uberadmin_token(auth_service, session, uberadmin.username)
    form = {"current_password": "wrong", "new_password": "BetterPass2!", "confirm_password": "BetterPass2!"}

    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        first = await client.post("/profile/password", data=form)
        second = await client.post("/profile/password", data=form)
        locked = await client.post("/profile/password", data={**form, "current_password": GOOD})

    assert first.status_code == 422 and "Current password is incorrect." in first.text
    assert second.status_code == 422
    assert _is_lockout(locked)
    await session.refresh(uberadmin)
    # The correct password was refused before being checked: nothing changed.
    assert await _uberadmin_token(auth_service, session, uberadmin.username)

    failures = await _events(session, "auth_failure")
    lockouts = await _events(session, "auth_throttle_lockout")
    assert [f["metadata"]["action"] for f in failures] == ["password_confirm", "password_confirm"]  # type: ignore[index]
    assert failures[0]["metadata"]["route"] == "profile_password"  # type: ignore[index]
    assert failures[0]["severity"] == "info" and failures[1]["severity"] == "warning"
    assert lockouts and lockouts[0]["severity"] == "warning" and lockouts[0]["actor_label"] == uberadmin.username


async def test_a_match_resets_the_budget_even_when_the_new_password_is_rejected(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cap(monkeypatch, account=2)
    app, auth_service = _build_profile_app(session)
    token = await _uberadmin_token(auth_service, session, uberadmin.username)

    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        await client.post(
            "/profile/password", data={"current_password": "wrong", "new_password": "x", "confirm_password": "x"}
        )
        # Correct current password, but a new password the policy rejects.
        weak = await client.post(
            "/profile/password", data={"current_password": GOOD, "new_password": "short", "confirm_password": "short"}
        )
        # The budget was reset by the match, so two more mistakes are needed to lock.
        again = await client.post(
            "/profile/password", data={"current_password": "wrong", "new_password": "x", "confirm_password": "x"}
        )

    assert weak.status_code == 422 and "Current password is incorrect." not in weak.text
    assert again.status_code == 422 and not _is_lockout(again)


# ---------------------------------------------------------------------------
# start-now / end-now
# ---------------------------------------------------------------------------


async def _admin_app(session: AsyncSession, uberadmin: UberAdmin, contest: Contest):  # type: ignore[no-untyped-def]
    admin = await admin_on(session, contest, uberadmin, "throttle-admin")
    await session.commit()  # the app opens its own sessions and sees only committed rows
    app, auth_service = build_contest_admin_app(session, routers=(contest_admin_router,))
    token = actor_token(auth_service, username=admin.username, contest_id=contest.id)
    return app, token


async def test_start_now_locks_and_refuses_the_correct_password(
    session: AsyncSession, uberadmin: UberAdmin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cap(monkeypatch, account=2)
    contest = _upcoming_contest(uberadmin)
    session.add(contest)
    await session.commit()
    app, token = await _admin_app(session, uberadmin, contest)

    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        first = await client.post(f"/c/{contest.login_slug}/admin/start-now", data={"password": "wrong"})
        second = await client.post(f"/c/{contest.login_slug}/admin/start-now", data={"password": "wrong"})
        locked = await client.post(f"/c/{contest.login_slug}/admin/start-now", data={"password": GOOD})

    assert first.status_code == 303, (first.status_code, first.headers.get("location"))
    assert second.status_code == 303
    assert _is_lockout(locked)
    await session.refresh(contest)
    assert not contest.is_running  # the correct password did not start the contest


async def test_end_now_happy_path_after_one_failure(
    session: AsyncSession, uberadmin: UberAdmin, running_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cap(monkeypatch, account=2)
    contest = running_contest
    await session.commit()
    app, token = await _admin_app(session, uberadmin, contest)

    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        await client.post(f"/c/{contest.login_slug}/admin/end-now", data={"password": "wrong"})
        ok = await client.post(f"/c/{contest.login_slug}/admin/end-now", data={"password": GOOD})

    assert ok.status_code == 303, (ok.status_code, ok.headers.get("location"))
    await session.refresh(contest)
    assert not contest.is_running


# ---------------------------------------------------------------------------
# uberadmin remove / export
# ---------------------------------------------------------------------------


def _upcoming_contest(uberadmin: UberAdmin) -> Contest:
    return Contest(
        contest_name="Throttle Contest",
        contest_url="http://throttle.example.com",
        login_slug="throttle-contest",
        start_time=datetime.now(UTC) + timedelta(days=1),
        duration_minutes=180,
        stop_answers_after=180,
        stop_updating_scoreboard=180,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )


async def _inactive(session: AsyncSession, contest: Contest) -> Contest:
    contest.active = False
    await session.commit()
    return contest


async def test_remove_refuses_the_correct_password_while_locked(
    session: AsyncSession, uberadmin: UberAdmin, stopped_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cap(monkeypatch, account=2)
    contest = await _inactive(session, stopped_contest)
    app, auth_service = build_uberadmin_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        for _ in range(2):
            wrong = await client.post(f"/uberadmin/contests/{contest.id}/remove", data={"password": "wrong"})
            assert wrong.status_code == 303
        locked = await client.post(f"/uberadmin/contests/{contest.id}/remove", data={"password": GOOD})

    assert _is_lockout(locked)
    assert await session.get(Contest, contest.id) is not None  # nothing was removed


async def test_export_throttles_only_hash_exports(
    session: AsyncSession, uberadmin: UberAdmin, stopped_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cap(monkeypatch, account=2)
    contest = stopped_contest
    await session.commit()
    app, auth_service = build_uberadmin_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)
    url = f"/uberadmin/contests/{contest.id}/export"

    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        for _ in range(2):
            wrong = await client.post(url, data={"include_password_hashes": "yes", "reconfirm_password": "wrong"})
            assert wrong.status_code == 422
        locked = await client.post(url, data={"include_password_hashes": "yes", "reconfirm_password": GOOD})
        # A hash-free export never touches the oracle, locked or not.
        plain = await client.post(url, data={"include_password_hashes": "no"})

    assert _is_lockout(locked)
    assert plain.status_code == 200
    assert plain.headers["content-type"] == "application/zip"


# ---------------------------------------------------------------------------
# One budget: routes, IPs, actors
# ---------------------------------------------------------------------------


async def test_failures_on_one_route_lock_another(
    session: AsyncSession, uberadmin: UberAdmin, stopped_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cap(monkeypatch, account=2)
    contest = await _inactive(session, stopped_contest)
    app, auth_service = build_uberadmin_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)
    profile_app, _ = _build_profile_app(session)

    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        for _ in range(2):
            await client.post(f"/uberadmin/contests/{contest.id}/remove", data={"password": "wrong"})
    async with _client(profile_app) as client:
        client.cookies.set("noca_access_token", token)
        locked = await client.post(
            "/profile/password",
            data={"current_password": GOOD, "new_password": "BetterPass2!", "confirm_password": "BetterPass2!"},
        )
    assert _is_lockout(locked)


async def test_rotating_ips_does_not_escape_the_actor_bucket(
    session: AsyncSession, uberadmin: UberAdmin, stopped_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cap(monkeypatch, account=2, ip=20)
    contest = await _inactive(session, stopped_contest)
    app, auth_service = build_uberadmin_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)
    url = f"/uberadmin/contests/{contest.id}/remove"

    async with _client(app, ip=IP_A) as a, _client(app, ip=IP_B) as b:
        a.cookies.set("noca_access_token", token)
        b.cookies.set("noca_access_token", token)
        await a.post(url, data={"password": "wrong"})
        await b.post(url, data={"password": "wrong"})
        locked = await a.post(url, data={"password": GOOD})
    assert _is_lockout(locked)


async def test_rotating_actors_does_not_escape_the_ip_bucket(
    session: AsyncSession, uberadmin: UberAdmin, stopped_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cap(monkeypatch, account=20, ip=2)
    contest = await _inactive(session, stopped_contest)
    other = UberAdmin(username="second-admin", fullname="Second Admin", email_normalizado="second@example.com")
    other.password = GOOD
    session.add(other)
    await session.commit()
    app, auth_service = build_uberadmin_app(session)
    token_a = await _login_uberadmin(auth_service, session, uberadmin.username)
    token_b = await _login_uberadmin(auth_service, session, other.username)
    url = f"/uberadmin/contests/{contest.id}/remove"

    async with _client(app) as client:
        client.cookies.set("noca_access_token", token_a)
        await client.post(url, data={"password": "wrong"})
        await client.post(url, data={"password": "wrong"})
        client.cookies.set("noca_access_token", token_b)
        locked = await client.post(url, data={"password": GOOD})
    assert _is_lockout(locked)


async def test_disabled_throttle_never_locks(
    session: AsyncSession, uberadmin: UberAdmin, stopped_contest: Contest, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cap(monkeypatch, account=1, enabled=False)
    contest = await _inactive(session, stopped_contest)
    app, auth_service = build_uberadmin_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)
    url = f"/uberadmin/contests/{contest.id}/remove"

    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        for _ in range(3):
            assert (await client.post(url, data={"password": "wrong"})).status_code == 303
    assert PASSWORD_CONFIRM_LIMITER._buckets == {}
