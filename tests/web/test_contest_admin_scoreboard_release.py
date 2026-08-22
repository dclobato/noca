#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the contest-admin final-scoreboard release control.

The control reveals every result held back by the freeze, and the flag it writes
is what the scoreboard, the team-submissions download and the animator all read.
It had no coverage of its own, which is what made the dashboard card that posts
to it unattractive to touch. The tests below pin the guards (ended contest only,
idempotent), the pre-warm of the permanent cache, the audit trail an irreversible
reveal must leave, and the dashboard states that reach the endpoint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.admin_audit import ADMIN_ACTION_EVENT_TYPE
from shared.services.scoreboard_cache import scoreboard_final_key
from shared.services.security_events import list_recent_security_events
from tests.web._contest_admin_test_support import actor_token, admin_on, build_contest_admin_app, dashboard_html
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.routes.contest_admin import router as contest_admin_router

_ROUTERS = (contest_admin_router,)
_EXTRA_SCOPED_STUBS = ("edit_metadata",)


async def _dashboard_html(session: AsyncSession, contest: Contest, admin: User) -> str:
    """Render the contest-admin dashboard as the given administrator."""
    return await dashboard_html(session, contest, admin, routers=_ROUTERS, extra_scoped_stub_names=_EXTRA_SCOPED_STUBS)


async def _post(session: AsyncSession, contest: Contest, admin: User) -> MagicMock:
    """Post one release command as a contest administrator, returning the fake Valkey."""
    app, auth_service = build_contest_admin_app(session, routers=_ROUTERS, extra_scoped_stub_names=_EXTRA_SCOPED_STUBS)
    valkey = MagicMock()
    valkey.get = AsyncMock(return_value=None)
    valkey.set = AsyncMock(return_value=None)
    valkey.delete = AsyncMock(return_value=None)
    app.state.valkey_runtime = valkey
    await session.commit()
    token = actor_token(auth_service, username=admin.username, contest_id=contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.post(
            f"/c/{contest.login_slug}/admin/release-scoreboard",
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"] == f"/c/{contest.login_slug}/admin/"
    await session.refresh(contest)
    return valkey


@pytest.mark.asyncio
async def test_release_reveals_the_final_standings(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """An ended contest's scoreboard is released and the permanent cache pre-warmed."""
    admin = await admin_on(session, stopped_contest, uberadmin, "admin_sb")

    valkey = await _post(session, stopped_contest, admin)

    assert stopped_contest.release_scoreboard_after_end is True
    # Pre-warmed under the permanent key, so the first public request is not the
    # one that pays for the computation.
    written = {call.args[0] for call in valkey.set.await_args_list}
    assert scoreboard_final_key(str(stopped_contest.id)) in written


@pytest.mark.asyncio
async def test_release_is_refused_while_the_contest_runs(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """Revealing pending results mid-contest would break the contest itself."""
    admin = await admin_on(session, running_contest, uberadmin, "admin_sb")

    valkey = await _post(session, running_contest, admin)

    # Both assertions pin the same refusal: the flag never flips, and the
    # route never even reaches the cache pre-warm that would call `valkey.set`.
    assert running_contest.release_scoreboard_after_end is False
    valkey.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeating_a_release_is_idempotent(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """A resubmitted form must not recompute or flip anything back."""
    admin = await admin_on(session, stopped_contest, uberadmin, "admin_sb")

    await _post(session, stopped_contest, admin)
    valkey = await _post(session, stopped_contest, admin)

    assert stopped_contest.release_scoreboard_after_end is True
    valkey.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_release_is_audited(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """Revealing every frozen result leaves a named trail, at warning severity.

    There is no endpoint that puts the freeze back, so this row is the only
    record of who made the standings public and when.
    """
    admin = await admin_on(session, stopped_contest, uberadmin, "admin_sb")

    await _post(session, stopped_contest, admin)

    events = await list_recent_security_events(session, limit=50, event_type=ADMIN_ACTION_EVENT_TYPE)
    # Narrowed to this contest's rows: the listing is not scoped by target, so
    # asserting over everything it returns would depend on what else ran.
    mine = [event for event in events if event.metadata.get("target_id") == stopped_contest.id]
    by_action = {event.metadata.get("action"): event for event in mine}
    assert "contest_scoreboard_release" in by_action
    assert by_action["contest_scoreboard_release"].severity == "warning"
    assert by_action["contest_scoreboard_release"].actor_label == admin.username


@pytest.mark.asyncio
async def test_a_refused_release_is_not_audited(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """The row commits with the flag, so a rejected attempt records nothing."""
    admin = await admin_on(session, running_contest, uberadmin, "admin_sb")

    await _post(session, running_contest, admin)

    events = await list_recent_security_events(session, limit=50, event_type=ADMIN_ACTION_EVENT_TYPE)
    mine = [event for event in events if event.metadata.get("target_id") == running_contest.id]
    assert not [event for event in mine if event.metadata.get("action") == "contest_scoreboard_release"]


@pytest.mark.asyncio
async def test_a_failed_cache_prewarm_leaves_no_flag_and_no_audit_row(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raise inside the cache pre-warm must roll back the flag and the audit row.

    The ordering in the route (audit, then pre-warm, then commit) relies on the
    pre-warm call being inside the same uncommitted transaction as the flag and
    the audit insert. This pins that contract instead of relying on the
    catch-and-warn behaviour inside ``get_or_compute_final`` happening to never
    raise in practice.
    """
    from web.routes import contest_admin as contest_admin_module

    admin = await admin_on(session, stopped_contest, uberadmin, "admin_sb")

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("cache backend unavailable")

    monkeypatch.setattr(contest_admin_module._score_service, "get_or_compute_final", _boom)

    app, auth_service = build_contest_admin_app(session, routers=_ROUTERS, extra_scoped_stub_names=_EXTRA_SCOPED_STUBS)
    valkey = MagicMock()
    valkey.get = AsyncMock(return_value=None)
    valkey.set = AsyncMock(return_value=None)
    valkey.delete = AsyncMock(return_value=None)
    app.state.valkey_runtime = valkey
    await session.commit()
    token = actor_token(auth_service, username=admin.username, contest_id=stopped_contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        with pytest.raises(RuntimeError):
            await client.post(
                f"/c/{stopped_contest.login_slug}/admin/release-scoreboard",
                follow_redirects=False,
            )
    await session.rollback()
    await session.refresh(stopped_contest)

    assert stopped_contest.release_scoreboard_after_end is False
    events = await list_recent_security_events(session, limit=50, event_type=ADMIN_ACTION_EVENT_TYPE)
    mine = [event for event in events if event.metadata.get("target_id") == stopped_contest.id]
    assert not [event for event in mine if event.metadata.get("action") == "contest_scoreboard_release"]


# ---------------------------------------------------------------------------
# Dashboard card
#
# The card is the only way an administrator reaches the endpoint, so its two
# states are pinned here alongside the route they drive.
# ---------------------------------------------------------------------------

_RELEASE_TEXT = "Release Final Scoreboard"
_RELEASED_TEXT = "Final Scoreboard Released"


@pytest.mark.asyncio
async def test_dashboard_offers_the_release_once_the_contest_ends(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """An ended, unreleased contest gets the release card and its confirmation."""
    admin = await admin_on(session, stopped_contest, uberadmin, "admin_sb")

    html = await _dashboard_html(session, stopped_contest, admin)

    assert _RELEASE_TEXT in html
    assert f"/c/{stopped_contest.login_slug}/admin/release-scoreboard" in html
    # Declared in markup and handled by the shared listener, never inline JS.
    assert "data-confirm=" in html
    assert "onsubmit=" not in html
    assert _RELEASED_TEXT not in html


@pytest.mark.asyncio
async def test_dashboard_reports_a_released_scoreboard(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """Once released, the card states the fact and offers no second release."""
    admin = await admin_on(session, stopped_contest, uberadmin, "admin_sb")
    stopped_contest.release_scoreboard_after_end = True

    html = await _dashboard_html(session, stopped_contest, admin)

    assert _RELEASED_TEXT in html
    assert _RELEASE_TEXT not in html


@pytest.mark.asyncio
async def test_dashboard_hides_the_card_while_the_contest_runs(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """The release is a post-contest decision, so nothing is offered before then."""
    admin = await admin_on(session, running_contest, uberadmin, "admin_sb")

    html = await _dashboard_html(session, running_contest, admin)

    assert _RELEASE_TEXT not in html
    assert _RELEASED_TEXT not in html
    # The actual contract is the absence of the form action, not just its label:
    # a regression that renders the form with the wrong copy would still pass
    # the two assertions above.
    assert f"/c/{running_contest.login_slug}/admin/release-scoreboard" not in html
