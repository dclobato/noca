#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Web announcement board: anonymous reads, UberAdmin-only writes, immutability, audit."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jinja2 import ChoiceLoader, FileSystemLoader
from markupsafe import escape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import security_events
from shared.enumerations import AnnouncementDomain
from shared.services.announcement_service import AnnouncementRow, create_announcement, get_announcement
from tests.web.test_inactive_contest_routes import _build_app as _build_base_app
from tests.web.test_inactive_contest_routes import _contest_token, _login_uberadmin
from web.routes.announcements import router as announcements_router
from web.routes.uberadmin_announcements import router as uberadmin_announcements_router

_ROOT = Path(__file__).resolve().parents[2]


def _build_app(session: AsyncSession) -> tuple[FastAPI, object]:
    """The reusable UberAdmin test app plus the announcement routers and the shared template loader."""
    app, auth_service = _build_base_app(session)
    # web/main.py resolves templates Web-first then shared; the helper builds a Web-only loader,
    # and the announcement pages include partials that live under shared/template.
    app.state.templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(str(_ROOT / "web" / "template")),
            FileSystemLoader(str(_ROOT / "shared" / "template")),
        ]
    )
    app.include_router(announcements_router)
    app.include_router(uberadmin_announcements_router)
    return app, auth_service


async def _publish(
    session: AsyncSession,
    *,
    domain: AnnouncementDomain = AnnouncementDomain.WEB,
    title: str = "Rating recalculation",
    body: str = "The **rating** changed. See [details](https://example.com) and $x^2$.",
) -> AnnouncementRow:
    row = await create_announcement(
        session,
        domain=domain,
        title=title,
        body=body,
        required=False,
        published_by_id="admin-1",
        published_by_label="root",
    )
    await session.commit()
    return row


async def _audit_rows(session: AsyncSession, action: str) -> list[dict[str, object]]:
    result = await session.execute(
        select(security_events.c.severity, security_events.c.metadata).where(
            security_events.c.module == "web",
            security_events.c.event_type == "admin_action",
        )
    )
    return [
        {"severity": severity, "metadata": metadata}
        for severity, metadata in result.all()
        if metadata.get("action") == action
    ]


@pytest.mark.asyncio
async def test_anonymous_list_links_each_row_to_its_detail_with_the_page(session: AsyncSession) -> None:
    row = await _publish(session)
    app, _ = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/announcements")

    assert response.status_code == 200
    assert f'id="announcement-{row.id}" class="noca-target-row"' in response.text
    assert f'href="http://testserver/announcements/{row.id}?page=1"' in response.text
    assert "Rating recalculation" in response.text


@pytest.mark.asyncio
async def test_anonymous_detail_binds_the_markdown_source_and_returns_to_the_same_page(
    session: AsyncSession,
) -> None:
    row = await _publish(session)
    app, _ = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/announcements/{row.id}?page=2")
        default_page = await client.get(f"/announcements/{row.id}")

    assert response.status_code == 200
    assert f'href="http://testserver/announcements?page=2#announcement-{row.id}"' in response.text
    assert f'href="http://testserver/announcements?page=1#announcement-{row.id}"' in default_page.text
    assert "Published by root" in response.text
    assert 'data-noca-markdown="announcement-body-src"' in response.text
    # The body reaches the browser as escaped source for the single pipeline, never as server HTML.
    assert "The **rating** changed." in response.text
    assert "<strong>rating</strong>" not in response.text
    for asset in ("marked.min.js", "purify.min.js", "katex.min.js", "auto-render.min.js", "mermaid.tiny.min.js"):
        assert asset in response.text


@pytest.mark.asyncio
async def test_an_arena_announcement_is_invisible_on_web(session: AsyncSession, uberadmin) -> None:
    row = await _publish(session, domain=AnnouncementDomain.ARENA)
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        detail = await client.get(f"/announcements/{row.id}")
        listing = await client.get("/announcements")
        client.cookies.set("noca_access_token", token)
        deletion = await client.post(f"/uberadmin/announcements/{row.id}/delete", data={"page": "1"})

    assert detail.status_code == 404
    assert row.id not in listing.text
    assert deletion.status_code == 404
    assert await get_announcement(session, domain=AnnouncementDomain.ARENA, announcement_id=row.id) is not None


@pytest.mark.asyncio
async def test_published_announcements_have_no_update_route(session: AsyncSession, uberadmin) -> None:
    row = await _publish(session, title="Original")
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        public_put = await client.put(f"/announcements/{row.id}", data={"title": "Changed"})
        admin_patch = await client.patch(f"/uberadmin/announcements/{row.id}", data={"title": "Changed"})
        admin_post = await client.post(f"/uberadmin/announcements/{row.id}", data={"title": "Changed"})
        management = await client.get("/uberadmin/announcements")

    assert public_put.status_code == 405
    assert admin_patch.status_code == 404
    assert admin_post.status_code == 404
    stored = await get_announcement(session, domain=AnnouncementDomain.WEB, announcement_id=row.id)
    assert stored is not None and stored.title == "Original"
    assert management.status_code == 200
    assert "edit" not in management.text.lower().split("announcement management", 1)[1].split("</table>", 1)[0]
    mutating = {
        (route.path, method)
        for route in app.routes
        if "announcement" in getattr(route, "path", "")
        for method in getattr(route, "methods", set())
        if method in {"PUT", "PATCH"}
    }
    assert mutating == set()
    assert not [route for route in app.routes if "edit" in getattr(route, "path", "") and "announcement" in route.path]


@pytest.mark.asyncio
async def test_the_contests_page_and_the_dashboard_link_to_the_board(session: AsyncSession, uberadmin) -> None:
    await session.commit()
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        contests = await client.get("/contests")
        client.cookies.set("noca_access_token", token)
        dashboard = await client.get("/uberadmin/")

    assert contests.status_code == 200
    assert 'href="http://testserver/announcements"' in contests.text
    assert dashboard.status_code == 200
    assert 'href="http://testserver/uberadmin/announcements"' in dashboard.text


@pytest.mark.asyncio
async def test_management_requires_an_uberadmin(session: AsyncSession, running_contest, admin_user) -> None:
    app, auth_service = _build_app(session)
    contest_admin = _contest_token(auth_service, username=admin_user.username, contest_id=running_contest.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        anonymous_get = await client.get("/uberadmin/announcements", follow_redirects=False)
        anonymous_post = await client.post(
            "/uberadmin/announcements", data={"title": "x", "body": "y"}, follow_redirects=False
        )
        client.cookies.set("noca_access_token", contest_admin)
        contest_get = await client.get("/uberadmin/announcements/new", follow_redirects=False)
        contest_post = await client.post(
            "/uberadmin/announcements", data={"title": "x", "body": "y"}, follow_redirects=False
        )
        listing = await client.get("/announcements")

    for response in (anonymous_get, anonymous_post, contest_get, contest_post):
        assert response.status_code in (302, 303)
        assert response.headers["location"].endswith("/contests")
    assert "announcement-" not in listing.text


@pytest.mark.asyncio
async def test_management_pages_render_the_editor_and_the_confirmed_delete(session: AsyncSession, uberadmin) -> None:
    row = await _publish(session)
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        listing = await client.get("/uberadmin/announcements")
        form = await client.get("/uberadmin/announcements/new")

    assert listing.status_code == 200
    assert 'href="http://testserver/uberadmin/announcements/new"' in listing.text
    assert f'action="http://testserver/uberadmin/announcements/{row.id}/delete"' in listing.text
    assert "data-confirm=" in listing.text
    assert form.status_code == 200
    assert 'id="announcement-body-editor"' in form.text
    assert 'action="http://testserver/uberadmin/announcements"' in form.text
    for asset in ("easymde.min.js", "problem-statement-editor-core.js", "announcement-editor.js"):
        assert asset in form.text


@pytest.mark.asyncio
async def test_create_publishes_audits_and_ignores_a_smuggled_required_flag(session: AsyncSession, uberadmin) -> None:
    await session.commit()
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.post(
            "/uberadmin/announcements",
            data={"title": "New problems", "body": "Ten [new problems](https://example.com).", "required": "true"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("http://testserver/uberadmin/announcements?page=1#announcement-")
    announcement_id = location.rsplit("#announcement-", 1)[1]
    stored = await get_announcement(session, domain=AnnouncementDomain.WEB, announcement_id=announcement_id)
    assert stored is not None
    assert stored.required is False
    assert stored.published_by_id == uberadmin.id
    assert stored.published_by_label == uberadmin.username
    audits = await _audit_rows(session, "publish")
    assert len(audits) == 1
    assert audits[0]["severity"] == "info"
    assert audits[0]["metadata"]["target_id"] == announcement_id  # type: ignore[index]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("title", "body", "message"),
    [
        ("", "A body", "Title is required."),
        ("A title", "Raw <b>html</b>", "disallowed content: html"),
    ],
)
async def test_a_refused_body_re_renders_the_form_and_writes_nothing(
    session: AsyncSession, uberadmin, title: str, body: str, message: str
) -> None:
    await session.commit()
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.post(
            "/uberadmin/announcements", data={"title": title, "body": body}, follow_redirects=False
        )
        listing = await client.get("/announcements")

    assert response.status_code == 200
    assert message in response.text
    assert 'id="announcement-body-editor"' in response.text
    assert str(escape(body)) in response.text
    assert "announcement-" not in listing.text
    assert await _audit_rows(session, "publish") == []


@pytest.mark.asyncio
async def test_delete_removes_audits_at_warning_and_is_not_repeatable(session: AsyncSession, uberadmin) -> None:
    row = await _publish(session)
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        first = await client.post(
            f"/uberadmin/announcements/{row.id}/delete", data={"page": "3"}, follow_redirects=False
        )
        second = await client.post(
            f"/uberadmin/announcements/{row.id}/delete", data={"page": "nonsense"}, follow_redirects=False
        )

    assert first.status_code == 303
    assert first.headers["location"] == "http://testserver/uberadmin/announcements?page=3"
    assert second.status_code == 404
    assert await get_announcement(session, domain=AnnouncementDomain.WEB, announcement_id=row.id) is None
    audits = await _audit_rows(session, "delete")
    assert len(audits) == 1
    assert audits[0]["severity"] == "warning"
