#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the contest-admin problem-set release control.

The control publishes or withdraws the anonymous ``GET /problem-set/{slug}.zip``
download, which carries every statement, secret test case, validator source and
editorial of the contest. The tests below pin the three properties that make it
safe to expose as a single click: the flag it writes is independent of the
scoreboard release, publishing is refused before the contest ends, and both
directions leave an audit trail.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.admin_audit import ADMIN_ACTION_EVENT_TYPE
from shared.services.security_events import list_recent_security_events
from tests.web._contest_admin_test_support import actor_token, admin_on, build_contest_admin_app, dashboard_html
from web.config import settings
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.routes.contest_admin import router as contest_admin_router
from web.routes.contest_admin_metadata import router as contest_admin_metadata_router
from web.services.problem_set_cache import cached_archive_path

_ROUTERS = (contest_admin_router, contest_admin_metadata_router)


async def _metadata_html(session: AsyncSession, contest: Contest, admin: User) -> str:
    """Render the contest metadata form as the given administrator."""
    app, auth_service = build_contest_admin_app(session, routers=_ROUTERS)
    await session.commit()
    token = actor_token(auth_service, username=admin.username, contest_id=contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get(f"/c/{contest.login_slug}/admin/metadata")
    assert response.status_code == 200
    return response.text


async def _dashboard_html(session: AsyncSession, contest: Contest, admin: User) -> str:
    """Render the contest-admin dashboard as the given administrator."""
    return await dashboard_html(session, contest, admin, routers=_ROUTERS)


async def _post(
    session: AsyncSession,
    contest: Contest,
    admin: User,
    release: str,
) -> None:
    """Post one release/revoke command as a contest administrator."""
    app, auth_service = build_contest_admin_app(session, routers=_ROUTERS)
    await session.commit()
    token = actor_token(auth_service, username=admin.username, contest_id=contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.post(
            f"/c/{contest.login_slug}/admin/release-problem-set",
            data={"release": release},
            follow_redirects=False,
        )
    assert response.status_code == 303
    await session.refresh(contest)


@pytest.mark.asyncio
async def test_release_publishes_without_touching_the_scoreboard(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """Publishing the problem set leaves the scoreboard embargoed."""
    admin = await admin_on(session, stopped_contest, uberadmin, "admin_ps")

    await _post(session, stopped_contest, admin, "yes")

    assert stopped_contest.release_problem_set_after_end is True
    assert stopped_contest.release_scoreboard_after_end is False


@pytest.mark.asyncio
async def test_revoke_withdraws_without_touching_the_scoreboard(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """Withdrawing the problem set leaves a released scoreboard released."""
    admin = await admin_on(session, stopped_contest, uberadmin, "admin_ps")
    stopped_contest.release_problem_set_after_end = True
    stopped_contest.release_scoreboard_after_end = True

    await _post(session, stopped_contest, admin, "no")

    assert stopped_contest.release_problem_set_after_end is False
    assert stopped_contest.release_scoreboard_after_end is True


@pytest.mark.asyncio
async def test_release_is_refused_while_the_contest_runs(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """Publishing needs the contest to be over; arming is the metadata form's job."""
    admin = await admin_on(session, running_contest, uberadmin, "admin_ps")

    await _post(session, running_contest, admin, "yes")

    assert running_contest.release_problem_set_after_end is False


@pytest.mark.asyncio
async def test_cancel_is_accepted_before_the_contest_ends(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """Withdrawing is always allowed: it doubles as Cancel on an armed contest."""
    admin = await admin_on(session, running_contest, uberadmin, "admin_ps")
    running_contest.release_problem_set_after_end = True

    await _post(session, running_contest, admin, "no")

    assert running_contest.release_problem_set_after_end is False


@pytest.mark.asyncio
async def test_repeating_a_release_is_idempotent(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """A resubmitted form must not flip the state back."""
    admin = await admin_on(session, stopped_contest, uberadmin, "admin_ps")

    await _post(session, stopped_contest, admin, "yes")
    await _post(session, stopped_contest, admin, "yes")

    assert stopped_contest.release_problem_set_after_end is True


@pytest.mark.asyncio
async def test_an_unknown_command_changes_nothing(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """``release`` is a strict yes/no string, never a coerced truthy value."""
    admin = await admin_on(session, stopped_contest, uberadmin, "admin_ps")

    await _post(session, stopped_contest, admin, "1")

    assert stopped_contest.release_problem_set_after_end is False


@pytest.mark.asyncio
async def test_both_directions_are_audited(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """Publishing and withdrawing secret material both leave a named trail."""
    admin = await admin_on(session, stopped_contest, uberadmin, "admin_ps")

    await _post(session, stopped_contest, admin, "yes")
    await _post(session, stopped_contest, admin, "no")

    events = await list_recent_security_events(session, limit=50, event_type=ADMIN_ACTION_EVENT_TYPE)
    # Narrowed to this contest's rows: the listing is not scoped by target, so
    # asserting over everything it returns would depend on what else ran.
    mine = [event for event in events if event.metadata.get("target_id") == stopped_contest.id]
    by_action = {event.metadata.get("action"): event for event in mine}
    assert "contest_problem_set_release" in by_action
    assert "contest_problem_set_revoke" in by_action
    # The publication is the consequential half and is recorded as such.
    assert by_action["contest_problem_set_release"].severity == "warning"
    assert by_action["contest_problem_set_revoke"].severity == "info"
    assert all(event.actor_label == admin.username for event in mine)


@pytest.mark.asyncio
async def test_revoke_drops_the_cached_archive(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A revoke clears the cache so a later re-release cannot serve a stale build."""
    admin = await admin_on(session, stopped_contest, uberadmin, "admin_ps")
    stopped_contest.release_problem_set_after_end = True
    monkeypatch.setattr(settings, "PUBLIC_PROBLEM_PACK_PATH", tmp_path)

    archive_path = cached_archive_path(tmp_path, stopped_contest)
    archive_path.write_bytes(b"stale archive")
    archive_path.with_suffix(".zip.sha256").write_text("0" * 64, encoding="ascii")

    await _post(session, stopped_contest, admin, "no")

    assert not archive_path.exists()
    assert not archive_path.with_suffix(".zip.sha256").exists()


# ---------------------------------------------------------------------------
# Dashboard cards
#
# The armed state is the safety property the whole arm-ahead design rests on: an
# admin who set the flag weeks earlier must see the pending publication on the
# page they already look at, and be able to call it off. A template regression
# that dropped the card would publish a contest's secrets with no warning ever
# shown, so all four cells of the state table are pinned here.
# ---------------------------------------------------------------------------

_ARMED_TEXT = "Publication Scheduled"
_RELEASED_TEXT = "Problem Set Released"
_RELEASE_TEXT = "Release Problem Set"


@pytest.mark.asyncio
async def test_dashboard_warns_and_offers_cancel_while_a_contest_is_armed(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """A running contest with the flag set shows the pending publication and a Cancel."""
    admin = await admin_on(session, running_contest, uberadmin, "admin_ps")
    running_contest.release_problem_set_after_end = True

    html = await _dashboard_html(session, running_contest, admin)

    assert _ARMED_TEXT in html
    assert "Publishes at contest end" in html
    assert "Nothing is public yet" in html
    assert 'name="release" value="no"' in html
    assert _RELEASE_TEXT not in html


@pytest.mark.asyncio
async def test_dashboard_shows_nothing_for_an_unarmed_running_contest(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """Arming lives in the metadata form, so a running contest offers no release card."""
    admin = await admin_on(session, running_contest, uberadmin, "admin_ps")

    html = await _dashboard_html(session, running_contest, admin)

    assert _ARMED_TEXT not in html
    assert _RELEASED_TEXT not in html
    assert _RELEASE_TEXT not in html


@pytest.mark.asyncio
async def test_dashboard_offers_release_for_an_unarmed_ended_contest(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """An ended contest that never armed the flag gets the explicit Release card."""
    admin = await admin_on(session, stopped_contest, uberadmin, "admin_ps")

    html = await _dashboard_html(session, stopped_contest, admin)

    assert _RELEASE_TEXT in html
    assert 'name="release" value="yes"' in html
    assert _ARMED_TEXT not in html


@pytest.mark.asyncio
async def test_dashboard_offers_revoke_once_the_problem_set_is_public(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """A released contest reports the public state and offers to withdraw it."""
    admin = await admin_on(session, stopped_contest, uberadmin, "admin_ps")
    stopped_contest.release_problem_set_after_end = True

    html = await _dashboard_html(session, stopped_contest, admin)

    assert _RELEASED_TEXT in html
    assert 'name="release" value="no"' in html
    assert _RELEASE_TEXT not in html


@pytest.mark.asyncio
async def test_metadata_form_offers_the_control_while_a_contest_runs(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """The metadata form is the arming surface, so it must render mid-contest.

    `is_locked` freezes most of that form once a contest starts. This control is
    exempt on purpose -- a post-contest publication decision is unreachable if it
    locks with the scoring rules -- and it lives in its own Publication section
    rather than among the judging criteria, where it read as a sixth scoring rule.
    """
    admin = await admin_on(session, running_contest, uberadmin, "admin_ps")

    html = await _metadata_html(session, running_contest, admin)

    assert 'name="release_problem_set_after_end"' in html
    assert "Publication" in html
    assert "Publish automatically once the contest ends" in html
    # Not disabled the way the locked scoring rules beside it are.
    yes_input = html.split('id="release_problem_set_after_end_yes"')[1].split(">")[0]
    assert "disabled" not in yes_input
    assert "every secret test case" in html


@pytest.mark.asyncio
async def test_metadata_form_reflects_a_contest_that_already_publishes(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """The stored value round-trips into the form's checked radio."""
    admin = await admin_on(session, stopped_contest, uberadmin, "admin_ps")
    stopped_contest.release_problem_set_after_end = True

    html = await _metadata_html(session, stopped_contest, admin)

    yes_block = html.split('id="release_problem_set_after_end_yes"')[1].split(">")[0]
    no_block = html.split('id="release_problem_set_after_end_no"')[1].split(">")[0]
    assert "checked" in yes_block
    assert "checked" not in no_block
