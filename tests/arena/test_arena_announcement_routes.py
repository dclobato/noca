#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena announcement board: anonymous reads, admin-only writes, immutability, audit.

The application comes from ``test_admin_categories._build_admin_app``: it carries
the admin-sidebar stubs every admin-dashboard page resolves, and the announcement
routers themselves arrive through ``mount_arena_base_routes`` (the sidebar
resolves them on every page), so they are deliberately not included twice.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from markupsafe import escape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db_schema import security_events
from shared.enumerations import AnnouncementDomain, ArenaRole
from shared.services.announcement_service import AnnouncementRow, create_announcement, get_announcement
from tests.arena.test_admin_categories import _build_admin_app, _create_arena_user, _login_token


async def _publish(
    session: AsyncSession,
    *,
    domain: AnnouncementDomain = AnnouncementDomain.ARENA,
    title: str = "New problems this week",
    body: str = "Ten **new** problems. See [the list](https://example.com) and $x^2$.",
    required: bool = False,
) -> AnnouncementRow:
    row = await create_announcement(
        session,
        domain=domain,
        title=title,
        body=body,
        required=required,
        published_by_id="admin-1",
        published_by_label="root@test.example",
    )
    await session.commit()
    return row


async def _audit_rows(session: AsyncSession, action: str) -> list[dict[str, object]]:
    result = await session.execute(
        select(security_events.c.severity, security_events.c.metadata).where(
            security_events.c.module == "arena",
            security_events.c.event_type == "admin_action",
        )
    )
    return [
        {"severity": severity, "metadata": metadata}
        for severity, metadata in result.all()
        if metadata.get("action") == action
    ]


@pytest.mark.asyncio
async def test_anonymous_list_renders_rows_and_the_sidebar_entry(session: AsyncSession) -> None:
    row = await _publish(session)
    app = _build_admin_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/announcements")

    assert response.status_code == 200
    assert f'id="announcement-{row.id}" class="noca-target-row"' in response.text
    assert f'href="http://testserver/announcements/{row.id}?page=1"' in response.text
    assert 'href="http://testserver/announcements"' in response.text
    assert "New problems this week" in response.text
    assert "arena-table" in response.text


@pytest.mark.asyncio
async def test_anonymous_detail_binds_the_markdown_and_returns_to_the_same_page(session: AsyncSession) -> None:
    row = await _publish(session)
    app = _build_admin_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/announcements/{row.id}?page=2")
        default_page = await client.get(f"/announcements/{row.id}")

    assert response.status_code == 200
    assert f'href="http://testserver/announcements?page=2#announcement-{row.id}"' in response.text
    assert f'href="http://testserver/announcements?page=1#announcement-{row.id}"' in default_page.text
    assert "Published by root@test.example" in response.text
    assert 'data-noca-markdown="announcement-body-src"' in response.text
    assert "Ten **new** problems." in response.text
    assert "<strong>new</strong>" not in response.text
    for asset in ("marked.min.js", "purify.min.js", "katex.min.js", "auto-render.min.js", "mermaid.tiny.min.js"):
        assert asset in response.text


@pytest.mark.asyncio
async def test_a_web_announcement_is_invisible_on_arena(session: AsyncSession) -> None:
    row = await _publish(session, domain=AnnouncementDomain.WEB)
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        detail = await client.get(f"/announcements/{row.id}")
        listing = await client.get("/announcements")
        client.cookies.set("arena_access_token", token)
        deletion = await client.post(f"/admin/announcements/{row.id}/delete", data={"page": "1"})

    assert detail.status_code == 404
    assert row.id not in listing.text
    assert deletion.status_code == 404
    assert await get_announcement(session, domain=AnnouncementDomain.WEB, announcement_id=row.id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [ArenaRole.ARENA_USER, ArenaRole.ARENA_JUDGE])
async def test_management_refuses_users_and_teachers(session: AsyncSession, role: ArenaRole) -> None:
    row = await _publish(session)
    app = _build_admin_app(session)
    user = await _create_arena_user(session, role=role, email=f"{role.value.lower()}@test.example")
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        listing = await client.get("/admin/announcements")
        form = await client.get("/admin/announcements/new")
        create = await client.post("/admin/announcements", data={"title": "x", "body": "y"})
        delete = await client.post(f"/admin/announcements/{row.id}/delete", data={"page": "1"})
        client.cookies.delete("arena_access_token")
        anonymous = await client.get("/admin/announcements")

    assert {listing.status_code, form.status_code, create.status_code, delete.status_code} == {403}
    assert anonymous.status_code == 401
    public = await get_announcement(session, domain=AnnouncementDomain.ARENA, announcement_id=row.id)
    assert public is not None
    assert await _audit_rows(session, "publish") == []
    assert await _audit_rows(session, "delete") == []


@pytest.mark.asyncio
async def test_management_pages_render_the_editor_the_flag_and_the_confirmed_delete(
    session: AsyncSession,
) -> None:
    row = await _publish(session)
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        listing = await client.get("/admin/announcements")
        form = await client.get("/admin/announcements/new")

    assert listing.status_code == 200
    assert 'href="http://testserver/admin/announcements/new"' in listing.text
    assert f'action="http://testserver/admin/announcements/{row.id}/delete"' in listing.text
    assert "data-confirm=" in listing.text
    assert 'href="http://testserver/admin/announcements"' in listing.text  # sidebar sub-entry
    management_table = listing.text.split("Announcement Management", 1)[1].split("</table>", 1)[0]
    assert "edit" not in management_table.lower()
    assert form.status_code == 200
    assert 'id="announcement-body-editor"' in form.text
    assert 'name="required"' in form.text
    assert "cannot be changed after publication" in form.text
    assert 'action="http://testserver/admin/announcements"' in form.text
    for asset in ("easymde.min.js", "problem-statement-editor-core.js", "announcement-editor.js"):
        assert asset in form.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("checkbox", "expected"), [({"required": "on"}, True), ({}, False)])
async def test_create_stores_the_required_flag_and_audits(
    session: AsyncSession, checkbox: dict[str, str], expected: bool
) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)
    body = "Rating recalculated. Details [here](https://example.com).\n\n```mermaid\ngraph TD\n  A-->B\n```"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.post(
            "/admin/announcements",
            data={"title": "Rating update", "body": body, **checkbox},
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("http://testserver/admin/announcements?page=1#announcement-")
    announcement_id = location.rsplit("#announcement-", 1)[1]
    stored = await get_announcement(session, domain=AnnouncementDomain.ARENA, announcement_id=announcement_id)
    assert stored is not None
    assert stored.required is expected
    assert stored.domain == "arena"
    assert stored.published_by_id == admin.id
    assert stored.published_by_label == admin.email_normalizado
    assert await get_announcement(session, domain=AnnouncementDomain.WEB, announcement_id=announcement_id) is None
    audits = await _audit_rows(session, "publish")
    assert len(audits) == 1
    assert audits[0]["severity"] == "info"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("title", "body", "message"),
    [
        ("", "A body", "Title is required."),
        ("A title", "Raw <b>html</b>", "disallowed content: html"),
    ],
)
async def test_a_refused_body_re_renders_the_form_and_writes_nothing(
    session: AsyncSession, title: str, body: str, message: str
) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.post(
            "/admin/announcements", data={"title": title, "body": body, "required": "on"}, follow_redirects=False
        )
        listing = await client.get("/announcements")

    assert response.status_code == 200
    assert message in response.text
    assert str(escape(body)) in response.text
    assert 'name="required"' in response.text and "checked" in response.text
    assert "announcement-" not in listing.text
    assert await _audit_rows(session, "publish") == []


@pytest.mark.asyncio
async def test_delete_audits_at_warning_and_is_not_repeatable(session: AsyncSession) -> None:
    row = await _publish(session, required=True)
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        first = await client.post(f"/admin/announcements/{row.id}/delete", data={"page": "2"}, follow_redirects=False)
        second = await client.post(
            f"/admin/announcements/{row.id}/delete", data={"page": "junk"}, follow_redirects=False
        )

    assert first.status_code == 303
    assert first.headers["location"] == "http://testserver/admin/announcements?page=2"
    assert second.status_code == 404
    assert await get_announcement(session, domain=AnnouncementDomain.ARENA, announcement_id=row.id) is None
    audits = await _audit_rows(session, "delete")
    assert len(audits) == 1
    assert audits[0]["severity"] == "warning"


@pytest.mark.asyncio
async def test_published_announcements_have_no_update_route(session: AsyncSession) -> None:
    row = await _publish(session, title="Original")
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        public_put = await client.put(f"/announcements/{row.id}", data={"title": "Changed"})
        admin_patch = await client.patch(f"/admin/announcements/{row.id}", data={"title": "Changed"})
        admin_post = await client.post(f"/admin/announcements/{row.id}", data={"title": "Changed"})

    assert public_put.status_code == 405
    assert admin_patch.status_code == 404
    assert admin_post.status_code == 404
    stored = await get_announcement(session, domain=AnnouncementDomain.ARENA, announcement_id=row.id)
    assert stored is not None and stored.title == "Original"
    announcement_routes = [route for route in app.routes if "announcement" in getattr(route, "path", "")]
    assert announcement_routes, "the announcement routers must be mounted through the base-route floor"
    assert not [
        route
        for route in announcement_routes
        if getattr(route, "methods", set()) & {"PUT", "PATCH"} or "edit" in route.path
    ]
