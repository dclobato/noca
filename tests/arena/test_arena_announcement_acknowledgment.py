#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The mandatory-announcement pop-up: when it renders, and the acknowledge route.

The application is ``test_admin_categories._build_admin_app`` with the real
``load_pending_required_announcement`` registered app-wide, exactly as
``arena/main.py`` does, so the modal is exercised through the production
dependency, the production template global and ``_base.html``.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.dependencies.required_announcements import load_pending_required_announcement
from arena.services.required_announcement_cache import required_announcements_known_absent
from shared.db_schema import arena_announcement_acknowledgments
from shared.enumerations import AnnouncementDomain, ArenaRole
from shared.services.announcement_service import AnnouncementRow, create_announcement
from tests.arena.test_admin_categories import _build_admin_app, _create_arena_user, _login_token

_MODAL = 'id="announcement-required-modal"'
#: What a browser navigation sends; a bare ``*/*`` (httpx's and fetch's default) is
#: deliberately *not* a page load for the pop-up dependency.
_BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


def _build_app(session: AsyncSession) -> FastAPI:
    return _build_admin_app(session, dependencies=[Depends(load_pending_required_announcement)])


async def _publish(
    session: AsyncSession,
    *,
    title: str = "Rules changed",
    domain: AnnouncementDomain = AnnouncementDomain.ARENA,
    required: bool = True,
) -> AnnouncementRow:
    row = await create_announcement(
        session,
        domain=domain,
        title=title,
        body="Please read the **new rules** and $x^2$.",
        required=required,
        published_by_id="admin-1",
        published_by_label="root@test.example",
    )
    await session.commit()
    return row


async def _ledger_size(session: AsyncSession) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(arena_announcement_acknowledgments))).scalar_one()
    )


@pytest.mark.asyncio
async def test_pending_required_announcement_pops_up_on_every_page(session: AsyncSession) -> None:
    row = await _publish(session)
    await _publish(session, title="Second one")
    app = _build_app(session)
    user = await _create_arena_user(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        board = await client.get("/announcements", headers=_BROWSER)
        legal = await client.get("/legal/terms", headers=_BROWSER)

    for response in (board, legal):
        assert response.status_code == 200
        assert _MODAL in response.text
        assert 'data-bs-backdrop="static"' in response.text
        assert 'data-bs-keyboard="false"' in response.text
        assert "Rules changed" in response.text
        assert 'data-noca-markdown="announcement-required-src"' in response.text
        assert "Please read the **new rules**" in response.text
        assert f'action="http://testserver/announcements/{row.id}/acknowledge"' in response.text
        assert 'id="announcement-acknowledge-submit"' in response.text
        assert "arena-announcement-required.js" in response.text
        assert "1 of 2" in response.text
        assert "Second one" not in response.text.split(_MODAL, 1)[1].split("</form>", 1)[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        (_BROWSER, True),
        ({**_BROWSER, "HX-Request": "true"}, False),
        ({"Accept": "application/json"}, False),
        ({"Accept": "*/*"}, False),
    ],
)
async def test_pop_up_only_on_browser_page_loads(
    session: AsyncSession, headers: dict[str, str], expected: bool
) -> None:
    await _publish(session)
    app = _build_app(session)
    user = await _create_arena_user(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        anonymous = await client.get("/announcements", headers=_BROWSER)
        client.cookies.set("arena_access_token", token)
        gated = await client.get("/announcements", headers=headers)

    assert anonymous.status_code == 200
    assert _MODAL not in anonymous.text
    assert gated.status_code == 200
    assert (_MODAL in gated.text) is expected


@pytest.mark.asyncio
async def test_no_pop_up_without_a_pending_required_announcement(session: AsyncSession) -> None:
    await _publish(session, required=False)
    await _publish(session, domain=AnnouncementDomain.WEB, required=True)
    app = _build_app(session)
    user = await _create_arena_user(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/announcements", headers=_BROWSER)

    assert response.status_code == 200
    assert _MODAL not in response.text


@pytest.mark.asyncio
async def test_page_loads_stop_querying_once_nothing_is_required(
    session: AsyncSession, sql_statements: list[str]
) -> None:
    await _publish(session, required=False)
    app = _build_app(session)
    user = await _create_arena_user(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        first = await client.get("/legal/terms", headers=_BROWSER)
        assert required_announcements_known_absent() is True
        sql_statements.clear()
        second = await client.get("/legal/terms", headers=_BROWSER)

    assert first.status_code == 200 and second.status_code == 200
    assert _MODAL not in second.text
    assert not [s for s in sql_statements if "announcements" in s.lower()]


@pytest.mark.asyncio
async def test_publishing_through_the_admin_route_shows_the_pop_up_at_once(session: AsyncSession) -> None:
    app = _build_app(session)
    user = await _create_arena_user(session)
    admin = await _create_arena_user(session, email="admin@test.example", role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, user)
    admin_token = _login_token(app, admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        before = await client.get("/legal/terms", headers=_BROWSER)
        assert required_announcements_known_absent() is True
        client.cookies.set("arena_access_token", admin_token)
        published = await client.post(
            "/admin/announcements",
            data={"title": "Mandatory now", "body": "Read it.", "required": "on"},
            follow_redirects=False,
        )
        client.cookies.set("arena_access_token", token)
        after = await client.get("/legal/terms", headers=_BROWSER)

    assert _MODAL not in before.text
    assert published.status_code == 303
    assert required_announcements_known_absent() is False
    assert _MODAL in after.text
    assert "Mandatory now" in after.text


@pytest.mark.asyncio
async def test_acknowledging_returns_to_the_page_and_clears_the_pop_up_for_that_user(session: AsyncSession) -> None:
    row = await _publish(session)
    app = _build_app(session)
    user = await _create_arena_user(session)
    other = await _create_arena_user(session, email="other@test.example")
    token = _login_token(app, user)
    other_token = _login_token(app, other)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        first = await client.post(
            f"/announcements/{row.id}/acknowledge",
            data={"acknowledge": "on"},
            headers={"Referer": "http://testserver/legal/terms"},
            follow_redirects=False,
        )
        second = await client.post(
            f"/announcements/{row.id}/acknowledge", data={"acknowledge": "on"}, follow_redirects=False
        )
        after = await client.get("/announcements", headers=_BROWSER)
        client.cookies.set("arena_access_token", other_token)
        other_page = await client.get("/announcements", headers=_BROWSER)

    assert first.status_code == 303
    assert first.headers["location"] == "/legal/terms"
    assert second.status_code == 303
    assert second.headers["location"] == "http://testserver/"
    assert await _ledger_size(session) == 1
    assert _MODAL not in after.text
    assert _MODAL in other_page.text


@pytest.mark.asyncio
async def test_acknowledge_refusals_write_nothing(session: AsyncSession) -> None:
    required = await _publish(session)
    optional = await _publish(session, title="Optional", required=False)
    web = await _publish(session, title="Web", domain=AnnouncementDomain.WEB, required=True)
    app = _build_app(session)
    user = await _create_arena_user(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        anonymous = await client.post(f"/announcements/{required.id}/acknowledge", data={"acknowledge": "on"})
        client.cookies.set("arena_access_token", token)
        unchecked = await client.post(f"/announcements/{required.id}/acknowledge", data={})
        not_required = await client.post(f"/announcements/{optional.id}/acknowledge", data={"acknowledge": "on"})
        web_domain = await client.post(f"/announcements/{web.id}/acknowledge", data={"acknowledge": "on"})
        unknown = await client.post("/announcements/nope/acknowledge", data={"acknowledge": "on"})

    assert anonymous.status_code == 401
    assert unchecked.status_code == 400
    assert not_required.status_code == 404
    assert web_domain.status_code == 404
    assert unknown.status_code == 404
    assert await _ledger_size(session) == 0


@pytest.mark.asyncio
async def test_management_list_marks_required_announcements(session: AsyncSession) -> None:
    await _publish(session, title="Mandatory one", required=True)
    await _publish(session, title="Plain one", required=False)
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/admin/announcements")

    assert response.status_code == 200
    mandatory_row = response.text.split("Mandatory one", 1)[1].split("</tr>", 1)[0]
    plain_row = response.text.split("Plain one", 1)[1].split("</tr>", 1)[0]
    assert ">Required</span>" in mandatory_row
    assert ">Required</span>" not in plain_row
