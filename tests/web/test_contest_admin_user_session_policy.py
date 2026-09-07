#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The organiser's controls for the single-session policy.

Two actions, deliberately different in kind. The contest-wide toggle is a
preference an organiser sets in advance; **Clear IP lock** is a release, used
mid-contest on a team that can no longer reach the seat it is bound to. The
release is password-confirmed and audited, and -- unlike **Remove** -- it stays
available *while the contest runs*, because a running contest is the only time a
lock exists at all.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import security_events
from shared.db_schema import users as users_t
from shared.enumerations import RoleEnum
from tests.shared._auth_fake_valkey import AuthFakeValkey
from tests.web._contest_admin_test_support import (
    actor_token,
    admin_on,
    build_contest_admin_app,
)
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.routes.contest_admin_reports import router as reports_router
from web.routes.contest_admin_user_edit import router as edit_router
from web.routes.contest_admin_user_session import router as session_router

#: Route names `enrolled.html` resolves that no router under test provides.
#:
#: The per-user links (`edit_user_form`, `remove_user_route`) take a `user_id`
#: the shared stub helper cannot express, so the real edit router is included
#: instead of stubbing them.
_ENROLLED_PAGE_STUBS = (
    "view",
    "add_user_form",
    "batch_import_form",
    "contest_admin_set_chief_judge",
)

_VENUE_IP = "203.0.113.10"
_ADMIN_PASSWORD = "TestPass1!"


async def _team(
    session: AsyncSession,
    contest: Contest,
    uberadmin: UberAdmin,
    username: str,
    *,
    restricted: bool = True,
    locked_ip: str | None = None,
) -> User:
    user = User(
        username=username,
        fullname=f"Team {username}",
        role=RoleEnum.TEAM,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
        allow_concurrent_login=not restricted,
        locked_ip=locked_ip,
        locked_at=datetime.now(UTC) if locked_ip else None,
    )
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


def _client(app: object) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")  # type: ignore[arg-type]


async def _post(app: object, auth_service: object, admin: User, url: str, data: dict[str, str]):
    token = actor_token(auth_service, username=admin.username, contest_id=admin.contest_id)  # type: ignore[arg-type]
    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        return await client.post(url, data=data, follow_redirects=False)


async def _events(session: AsyncSession) -> list[tuple[str, str, str | None]]:
    rows = await session.execute(
        select(security_events.c.event_type, security_events.c.severity, security_events.c["metadata"])
    )
    return [(row[0], row[1], row[2]) for row in rows]


@pytest.mark.asyncio
async def test_clearing_a_lock_releases_the_seat_and_supersedes_the_sessions(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """The point of the button: a team that changed seats can sign in again.

    The epoch moves too. Clearing the address alone would leave every session
    bound to it still valid, and the first of them to make a request would
    re-bind the very address the operator just released.
    """
    admin = await admin_on(session, running_contest, uberadmin, "org")
    team = await _team(session, running_contest, uberadmin, "t1", locked_ip=_VENUE_IP)
    await session.commit()
    epoch_before = team.session_epoch
    app, auth_service = build_contest_admin_app(session, routers=(session_router,))
    app.state.valkey_runtime = AuthFakeValkey()

    response = await _post(
        app,
        auth_service,
        admin,
        f"/c/{running_contest.login_slug}/admin/users/{team.id}/clear-ip-lock",
        {"password": _ADMIN_PASSWORD},
    )

    assert response.status_code == 303
    await session.refresh(team)
    assert team.locked_ip is None
    assert team.locked_at is None
    assert team.session_epoch > epoch_before, "the sessions bound to the old address are superseded"


@pytest.mark.asyncio
async def test_clearing_a_lock_is_audited_at_warning_severity_naming_the_address(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """The audit row is the only record that a running contest's team was helped."""
    admin = await admin_on(session, running_contest, uberadmin, "org")
    team = await _team(session, running_contest, uberadmin, "t1", locked_ip=_VENUE_IP)
    await session.commit()
    app, auth_service = build_contest_admin_app(session, routers=(session_router,))
    app.state.valkey_runtime = AuthFakeValkey()

    await _post(
        app,
        auth_service,
        admin,
        f"/c/{running_contest.login_slug}/admin/users/{team.id}/clear-ip-lock",
        {"password": _ADMIN_PASSWORD},
    )

    audit = [row for row in await _events(session) if row[0] == "admin_action"]
    assert audit, "the release is recorded"
    assert audit[0][1] == "warning"
    assert _VENUE_IP in str(audit[0][2])
    assert "clear_ip_lock" in str(audit[0][2])


@pytest.mark.asyncio
async def test_a_wrong_password_changes_nothing(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    admin = await admin_on(session, running_contest, uberadmin, "org")
    team = await _team(session, running_contest, uberadmin, "t1", locked_ip=_VENUE_IP)
    await session.commit()
    app, auth_service = build_contest_admin_app(session, routers=(session_router,))
    app.state.valkey_runtime = AuthFakeValkey()

    response = await _post(
        app,
        auth_service,
        admin,
        f"/c/{running_contest.login_slug}/admin/users/{team.id}/clear-ip-lock",
        {"password": "not-the-password"},
    )

    assert response.status_code == 303
    await session.refresh(team)
    assert team.locked_ip == _VENUE_IP


@pytest.mark.asyncio
async def test_a_user_of_another_contest_is_not_reachable(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """The route is contest-scoped, so a foreign id is a 404 rather than a release."""
    other = Contest(
        contest_name="Other",
        contest_url="http://other.example.com",
        login_slug="other-contest",
        start_time=datetime.now(UTC),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(other)
    await session.flush()
    admin = await admin_on(session, running_contest, uberadmin, "org")
    stranger = await _team(session, other, uberadmin, "t1", locked_ip=_VENUE_IP)
    await session.commit()
    app, auth_service = build_contest_admin_app(session, routers=(session_router,))
    app.state.valkey_runtime = AuthFakeValkey()

    response = await _post(
        app,
        auth_service,
        admin,
        f"/c/{running_contest.login_slug}/admin/users/{stranger.id}/clear-ip-lock",
        {"password": _ADMIN_PASSWORD},
    )

    assert response.status_code == 404
    await session.refresh(stranger)
    assert stranger.locked_ip == _VENUE_IP


@pytest.mark.asyncio
async def test_the_contest_toggle_restricts_teams_only(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """Staff are exempt by role, so setting their flag would imply a rule that is not there."""
    admin = await admin_on(session, running_contest, uberadmin, "org")
    team = await _team(session, running_contest, uberadmin, "t1", restricted=False)
    judge = User(
        username="j1",
        fullname="Judge",
        role=RoleEnum.JUDGE,
        contest_id=running_contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    judge.password = "TestPass1!"
    session.add(judge)
    await session.commit()
    app, auth_service = build_contest_admin_app(session, routers=(session_router,))
    app.state.valkey_runtime = AuthFakeValkey()

    response = await _post(
        app,
        auth_service,
        admin,
        f"/c/{running_contest.login_slug}/admin/users/session-policy",
        {"restrict": "true"},
    )

    assert response.status_code == 303
    await session.refresh(team)
    await session.refresh(judge)
    assert team.allow_concurrent_login is False
    assert judge.allow_concurrent_login is True, "staff are left alone"
    assert admin.allow_concurrent_login is True


@pytest.mark.asyncio
async def test_lifting_the_toggle_releases_the_bindings_it_made(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """A lift means the rule is off and the addresses it recorded are gone.

    Keeping them was the original decision, and it turned the round trip into a
    trap: re-applying the rule enforced addresses captured before the lift, so
    every team that had moved was refused at exactly the moment an organiser was
    trying to let them back in.
    """
    admin = await admin_on(session, running_contest, uberadmin, "org")
    team = await _team(session, running_contest, uberadmin, "t1", locked_ip=_VENUE_IP)
    await session.commit()
    epoch_before = team.session_epoch
    app, auth_service = build_contest_admin_app(session, routers=(session_router,))
    app.state.valkey_runtime = AuthFakeValkey()

    await _post(
        app,
        auth_service,
        admin,
        f"/c/{running_contest.login_slug}/admin/users/session-policy",
        {"restrict": "false"},
    )

    await session.refresh(team)
    assert team.allow_concurrent_login is True
    assert team.locked_ip is None
    assert team.locked_at is None
    assert team.session_epoch == epoch_before, "nobody is signed out: the rule simply stopped applying"


@pytest.mark.asyncio
async def test_the_round_trip_binds_wherever_the_team_is_then(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """Lift then re-apply is a fresh start, which is the point of releasing on lift."""
    admin = await admin_on(session, running_contest, uberadmin, "org")
    team = await _team(session, running_contest, uberadmin, "t1", locked_ip=_VENUE_IP)
    await session.commit()
    app, auth_service = build_contest_admin_app(session, routers=(session_router,))
    app.state.valkey_runtime = AuthFakeValkey()
    url = f"/c/{running_contest.login_slug}/admin/users/session-policy"

    await _post(app, auth_service, admin, url, {"restrict": "false"})
    await _post(app, auth_service, admin, url, {"restrict": "true"})

    await session.refresh(team)
    assert team.allow_concurrent_login is False, "governed again"
    assert team.locked_ip is None, "and free to bind wherever it now is"


@pytest.mark.asyncio
@pytest.mark.parametrize("restrict", ["", "on", "1", "yes", "TRUE"])
async def test_the_toggle_refuses_anything_but_the_two_exact_values(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin, restrict: str
) -> None:
    """A strict string, so a half-submitted form cannot be coerced into a policy change."""
    admin = await admin_on(session, running_contest, uberadmin, "org")
    team = await _team(session, running_contest, uberadmin, "t1", restricted=False)
    await session.commit()
    app, auth_service = build_contest_admin_app(session, routers=(session_router,))
    app.state.valkey_runtime = AuthFakeValkey()

    response = await _post(
        app,
        auth_service,
        admin,
        f"/c/{running_contest.login_slug}/admin/users/session-policy",
        {"restrict": restrict},
    )

    assert response.status_code == 422
    await session.refresh(team)
    assert team.allow_concurrent_login is True


@pytest.mark.asyncio
async def test_the_enrolled_page_offers_the_release_while_the_contest_runs(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """Remove is hidden during a contest; this must not be, or a lock is unrecoverable."""
    admin = await admin_on(session, running_contest, uberadmin, "org")
    bound = await _team(session, running_contest, uberadmin, "bound", locked_ip=_VENUE_IP)
    await _team(session, running_contest, uberadmin, "free", restricted=False)
    await session.commit()
    app, auth_service = build_contest_admin_app(
        session,
        routers=(reports_router, session_router, edit_router),
        extra_scoped_stub_names=_ENROLLED_PAGE_STUBS,
    )
    app.state.valkey_runtime = AuthFakeValkey()
    token = actor_token(auth_service, username=admin.username, contest_id=running_contest.id)

    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        page = await client.get(f"/c/{running_contest.login_slug}/admin/users")

    assert page.status_code == 200
    assert running_contest.is_running is True
    assert "Clear IP lock" in page.text
    assert f"/admin/users/{bound.id}/clear-ip-lock" in page.text
    assert _VENUE_IP in page.text, "the row says which address the team is bound to"


@pytest.mark.asyncio
async def test_an_unbound_team_gets_no_release_button(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    admin = await admin_on(session, running_contest, uberadmin, "org")
    free = await _team(session, running_contest, uberadmin, "free", restricted=False)
    await session.commit()
    app, auth_service = build_contest_admin_app(
        session,
        routers=(reports_router, session_router, edit_router),
        extra_scoped_stub_names=_ENROLLED_PAGE_STUBS,
    )
    app.state.valkey_runtime = AuthFakeValkey()
    token = actor_token(auth_service, username=admin.username, contest_id=running_contest.id)

    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        page = await client.get(f"/c/{running_contest.login_slug}/admin/users")

    assert f"/admin/users/{free.id}/clear-ip-lock" not in page.text


@pytest.mark.asyncio
async def test_a_release_on_an_already_unbound_user_is_reported_not_failed(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """Another administrator may have released it first; that is not an error."""
    admin = await admin_on(session, running_contest, uberadmin, "org")
    team = await _team(session, running_contest, uberadmin, "t1")
    await session.commit()
    epoch_before = team.session_epoch
    app, auth_service = build_contest_admin_app(session, routers=(session_router,))
    app.state.valkey_runtime = AuthFakeValkey()

    response = await _post(
        app,
        auth_service,
        admin,
        f"/c/{running_contest.login_slug}/admin/users/{team.id}/clear-ip-lock",
        {"password": _ADMIN_PASSWORD},
    )

    assert response.status_code == 303
    await session.refresh(team)
    assert team.session_epoch == epoch_before, "nothing was superseded"
    assert not [row for row in await _events(session) if row[0] == "admin_action"]


@pytest.mark.asyncio
async def test_the_bulk_statement_reports_only_the_rows_it_changed(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """A repeated click must not claim to have changed the whole contest again."""
    admin = await admin_on(session, running_contest, uberadmin, "org")
    await _team(session, running_contest, uberadmin, "t1", restricted=False)
    await _team(session, running_contest, uberadmin, "t2", restricted=True)
    await session.commit()
    app, auth_service = build_contest_admin_app(session, routers=(session_router,))
    app.state.valkey_runtime = AuthFakeValkey()
    url = f"/c/{running_contest.login_slug}/admin/users/session-policy"

    await _post(app, auth_service, admin, url, {"restrict": "true"})
    audit = [row for row in await _events(session) if row[0] == "admin_action"]
    assert "teams_changed=1" in str(audit[-1][2]), "only the one team that was not already restricted"

    await _post(app, auth_service, admin, url, {"restrict": "true"})
    audit = [row for row in await _events(session) if row[0] == "admin_action"]
    assert "teams_changed=0" in str(audit[-1][2])

    restricted = await session.scalar(
        select(users_t.c.allow_concurrent_login).where(users_t.c.contest_id == running_contest.id).limit(1)
    )
    assert restricted is not None


@pytest.mark.asyncio
async def test_lifting_the_rule_reports_the_locks_it_released(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """The operator is told what the lift did, not just that the flag moved."""
    admin = await admin_on(session, running_contest, uberadmin, "org")
    await _team(session, running_contest, uberadmin, "bound", locked_ip=_VENUE_IP)
    await _team(session, running_contest, uberadmin, "free")
    await session.commit()
    app, auth_service = build_contest_admin_app(
        session,
        routers=(reports_router, session_router, edit_router),
        extra_scoped_stub_names=_ENROLLED_PAGE_STUBS,
    )
    app.state.valkey_runtime = AuthFakeValkey()
    token = actor_token(auth_service, username=admin.username, contest_id=running_contest.id)

    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        await client.post(
            f"/c/{running_contest.login_slug}/admin/users/session-policy",
            data={"restrict": "false"},
            follow_redirects=False,
        )
        landing = await client.get(f"/c/{running_contest.login_slug}/admin/users")

    assert "1 IP lock(s) were released" in landing.text
    assert "bind each team wherever it is then" in landing.text


@pytest.mark.asyncio
async def test_lifting_an_unbound_contest_mentions_no_locks(
    session: AsyncSession, running_contest: Contest, uberadmin: UberAdmin
) -> None:
    """No bindings, no sentence about them."""
    admin = await admin_on(session, running_contest, uberadmin, "org")
    await _team(session, running_contest, uberadmin, "free")
    await session.commit()
    app, auth_service = build_contest_admin_app(
        session,
        routers=(reports_router, session_router, edit_router),
        extra_scoped_stub_names=_ENROLLED_PAGE_STUBS,
    )
    app.state.valkey_runtime = AuthFakeValkey()
    token = actor_token(auth_service, username=admin.username, contest_id=running_contest.id)

    async with _client(app) as client:
        client.cookies.set("noca_access_token", token)
        await client.post(
            f"/c/{running_contest.login_slug}/admin/users/session-policy",
            data={"restrict": "false"},
            follow_redirects=False,
        )
        landing = await client.get(f"/c/{running_contest.login_slug}/admin/users")

    assert "IP lock(s) were released" not in landing.text
