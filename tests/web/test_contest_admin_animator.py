#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the contest-admin animator settings page."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient, Response
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from shared.enumerations import RoleEnum
from shared.services import animator_access_service
from shared.services.admin_audit import ADMIN_ACTION_EVENT_TYPE
from shared.services.animator_access_service import digest_token
from shared.services.email_service import EmailConfig, EmailService
from shared.services.security_events import list_recent_security_events
from web.models.contest import Contest
from web.models.site import Site
from web.models.users import UberAdmin, User
from web.routes.assets import router as assets_router
from web.routes.contest_admin_animator import router as animator_router
from web.services.authentication_service import AuthAction, AuthenticationService

TEST_JWT_SECRET = "test-secret-key-for-tests-only-32bytes"


class _NoopGeo:
    """Test geolocation service that never resolves an address."""

    def get_details_by_ip(self, ip_address: str | None):  # noqa: ANN201 - test stub
        """Return no geolocation details for any address."""
        return None


def _build_app(session: AsyncSession) -> tuple[FastAPI, AuthenticationService]:
    """Build the smallest application that exercises animator routes."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    web_dir = Path(__file__).resolve().parents[2] / "web"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=web_dir / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["RoleEnum"] = RoleEnum
    templates.env.globals["role_labels"] = {role.value: role.value.title() for role in RoleEnum}
    setup_flash(templates)
    app.state.templates = templates
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.email_service = EmailService(
        config=EmailConfig(
            send_email=False,
            provider_type="mock",
            default_from_email="no-reply@example.com",
            default_from_name="NOCA",
            smtp_server=None,
            smtp_port=587,
            smtp_username=None,
            smtp_password=None,
            smtp_use_tls=True,
        ),
        logger=logging.getLogger(__name__),
    )

    from shared.services.geolocation import GeolocationIP

    jwt_service = JWTService(
        config=load_token_config_from_dict(
            {
                "SECRET_KEY": TEST_JWT_SECRET,
                "JWTSERVICE_ALGORITHM": "HS256",
                "JWTSERVICE_ISSUER": "noca-test",
            }
        ),
        logger=logging.getLogger(__name__),
        action_enum=AuthAction,
    )
    app.state.auth_service = AuthenticationService(
        jwt_service=jwt_service,
        geolocation_service=cast(GeolocationIP, _NoopGeo()),
        logger=logging.getLogger(__name__),
    )

    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/css", StaticFiles(directory=web_dir / "static" / "css"), name="static_css")
    app.mount("/static/js", StaticFiles(directory=web_dir / "static" / "js"), name="static_js")
    app.mount("/static/shared/js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")

    @app.get("/c/{slug}/admin", name="view")
    async def _admin_home(slug: str) -> dict[str, str]:
        """Provide the admin-home route required by breadcrumbs."""
        return {"slug": slug}

    @app.get("/c/{slug}/admin/metadata", name="edit_metadata")
    async def _edit_metadata(slug: str) -> dict[str, str]:
        """Provide the metadata route required by the empty state."""
        return {"slug": slug}

    @app.get("/c/{slug}/", name="contest_dashboard")
    async def _contest_dashboard(slug: str) -> dict[str, str]:
        """Provide the contest dashboard route required by breadcrumbs."""
        return {"slug": slug}

    @app.get("/uberadmin/", name="uberadmin_dashboard")
    async def _uberadmin_dashboard() -> dict[str, str]:
        """Provide the UberAdmin route required by breadcrumbs."""
        return {"ok": "ok"}

    @app.get("/c/{slug}/clock", name="contest_clock")
    async def _contest_clock(slug: str) -> dict[str, str]:
        """Provide the contest clock route required by the base template."""
        return {"slug": slug}

    @app.get("/profile", name="profile_get")
    async def _profile() -> dict[str, str]:
        """Provide the profile route required by the base template."""
        return {"ok": "ok"}

    @app.get("/logout", name="logout")
    async def _logout() -> dict[str, str]:
        """Provide the logout route required by the base template."""
        return {"ok": "ok"}

    app.include_router(assets_router)
    app.include_router(animator_router)
    return app, app.state.auth_service


def _actor_token(auth_service: AuthenticationService, *, username: str, role: RoleEnum, contest_id: str) -> str:
    """Create a contest-scoped access token for a test actor."""
    return auth_service.jwt_service.create(
        action=AuthAction.WEB_ACCESS,
        sub=username,
        audience=role.value,
        extra_data={"contest_id": contest_id, "session_started_at": int(datetime.now(UTC).timestamp())},
    )


def _assert_partial(response: Response) -> None:
    """Assert a response contains only the self-replacing operators partial."""
    assert response.status_code == 200
    assert response.text.lstrip().startswith('<div id="animator-operators"')
    assert response.text.count('id="animator-operators"') == 1
    assert "<html" not in response.text
    assert "breadcrumb" not in response.text


def _extract_secret(response: Response) -> str:
    """Extract the one-time plaintext token from a create response."""
    match = re.search(r'id="new-secret-value"[^>]*>([^<]+)</code>', response.text)
    assert match is not None
    return match.group(1).strip()


async def _make_site(session: AsyncSession, contest: Contest, name: str) -> Site:
    """Persist a contest site for route tests."""
    site = Site(sitename=name, sitename_normalized=name.lower(), contest_id=contest.id)
    session.add(site)
    await session.flush()
    return site


async def _other_contest(session: AsyncSession, uberadmin: UberAdmin) -> Contest:
    """Persist a second contest for cross-contest scope tests."""
    contest = Contest(
        contest_name="Other",
        contest_url="https://other.example.com",
        login_slug="other",
        start_time=datetime.now(UTC) - timedelta(minutes=10),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=120,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    return contest


@pytest.mark.asyncio
async def test_unauthenticated_access_redirects(session: AsyncSession, running_contest: Contest) -> None:
    """Redirect an unauthenticated request to login."""
    app, _ = _build_app(session)
    await session.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/c/{running_contest.login_slug}/admin/animator/", follow_redirects=False)
    assert response.status_code in (302, 303, 307)


@pytest.mark.asyncio
async def test_non_admin_forbidden(session: AsyncSession, running_contest: Contest, team_user: User) -> None:
    """Reject a contest actor without administrator privileges."""
    app, auth_service = _build_app(session)
    await session.commit()
    token = _actor_token(auth_service, username=team_user.username, role=RoleEnum.TEAM, contest_id=running_contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get(f"/c/{running_contest.login_slug}/admin/animator/")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_page_has_one_operators_target_and_toggle(
    session: AsyncSession, running_contest: Contest, admin_user: User
) -> None:
    """Render one HTMX target and preserve the PRG access toggle."""
    await _make_site(session, running_contest, "Site A")
    app, auth_service = _build_app(session)
    await session.commit()
    token = _actor_token(auth_service, username=admin_user.username, role=RoleEnum.ADMIN, contest_id=running_contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        page = await client.get(f"/c/{running_contest.login_slug}/admin/animator/")
        toggle = await client.post(
            f"/c/{running_contest.login_slug}/admin/animator/settings",
            data={"animator_enabled": "yes"},
            follow_redirects=False,
        )

    assert page.status_code == 200
    assert page.text.count('id="animator-operators"') == 1
    shared_clipboard_position = page.text.index("clipboard.js?v=test")
    animator_settings_position = page.text.index("animator-settings.js?v=test")
    assert shared_clipboard_position < animator_settings_position
    assert "Site A" in page.text
    assert 'name="style"' not in page.text
    for band, label in (("gold", "Gold"), ("silver", "Silver"), ("bronze", "Bronze")):
        assert f'src="http://testserver/assets/medal/{band}"' in page.text
        assert f'<span class="animator-medal-heading-label">{label}</span>' in page.text
        assert f"{label} ≤" not in page.text
    assert toggle.status_code == 303
    await session.refresh(running_contest)
    assert running_contest.animator_enabled is True


@pytest.mark.asyncio
async def test_bulk_medal_update_persists_all_sites(
    session: AsyncSession, running_contest: Contest, admin_user: User
) -> None:
    """Persist every valid row through one bulk medals form."""
    first = await _make_site(session, running_contest, "Site A")
    second = await _make_site(session, running_contest, "Site B")
    app, auth_service = _build_app(session)
    await session.commit()
    token = _actor_token(auth_service, username=admin_user.username, role=RoleEnum.ADMIN, contest_id=running_contest.id)
    data = {
        f"gold_cutoff_{first.id}": "2",
        f"silver_cutoff_{first.id}": "5",
        f"bronze_cutoff_{first.id}": "8",
        f"gold_cutoff_{second.id}": "3",
        f"silver_cutoff_{second.id}": "6",
        f"bronze_cutoff_{second.id}": "9",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.post(
            f"/c/{running_contest.login_slug}/admin/animator/medals", data=data, follow_redirects=False
        )
    assert response.status_code == 303
    await session.refresh(first)
    await session.refresh(second)
    assert (first.gold_cutoff, first.silver_cutoff, first.bronze_cutoff) == (2, 5, 8)
    assert (second.gold_cutoff, second.silver_cutoff, second.bronze_cutoff) == (3, 6, 9)


@pytest.mark.asyncio
async def test_bulk_medal_update_is_all_or_nothing(
    session: AsyncSession, running_contest: Contest, admin_user: User
) -> None:
    """Reject every row when any submitted site's cutoffs are invalid."""
    first = await _make_site(session, running_contest, "Site A")
    second = await _make_site(session, running_contest, "Site B")
    app, auth_service = _build_app(session)
    await session.commit()
    token = _actor_token(auth_service, username=admin_user.username, role=RoleEnum.ADMIN, contest_id=running_contest.id)
    data = {
        f"gold_cutoff_{first.id}": "4",
        f"silver_cutoff_{first.id}": "8",
        f"bronze_cutoff_{first.id}": "12",
        f"gold_cutoff_{second.id}": "5",
        f"silver_cutoff_{second.id}": "2",
        f"bronze_cutoff_{second.id}": "9",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.post(
            f"/c/{running_contest.login_slug}/admin/animator/medals", data=data, follow_redirects=False
        )
    assert response.status_code == 303
    await session.refresh(first)
    await session.refresh(second)
    assert (first.gold_cutoff, first.silver_cutoff, first.bronze_cutoff) == (1, 2, 3)
    assert (second.gold_cutoff, second.silver_cutoff, second.bronze_cutoff) == (1, 2, 3)


@pytest.mark.asyncio
async def test_bulk_medal_update_reports_site_added_after_page_render(
    session: AsyncSession, running_contest: Contest, admin_user: User
) -> None:
    """Reject a stale medals form with a clear missing-site message."""
    original_site = await _make_site(session, running_contest, "Site A")
    app, auth_service = _build_app(session)
    await session.commit()
    token = _actor_token(auth_service, username=admin_user.username, role=RoleEnum.ADMIN, contest_id=running_contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        rendered_page = await client.get(f"/c/{running_contest.login_slug}/admin/animator/")
        new_site = await _make_site(session, running_contest, "Site B")
        await session.commit()
        submitted = await client.post(
            f"/c/{running_contest.login_slug}/admin/animator/medals",
            data={
                f"gold_cutoff_{original_site.id}": "4",
                f"silver_cutoff_{original_site.id}": "8",
                f"bronze_cutoff_{original_site.id}": "12",
            },
            follow_redirects=False,
        )
        feedback_page = await client.get(submitted.headers["location"])

    assert rendered_page.status_code == 200
    assert submitted.status_code == 303
    assert "Medal settings are missing for site Site B. Reload the page and try again." in feedback_page.text
    await session.refresh(original_site)
    await session.refresh(new_site)
    assert (original_site.gold_cutoff, original_site.silver_cutoff, original_site.bronze_cutoff) == (1, 2, 3)
    assert (new_site.gold_cutoff, new_site.silver_cutoff, new_site.bronze_cutoff) == (1, 2, 3)


@pytest.mark.asyncio
async def test_site_credential_returns_partial_once_and_emails_admin(
    session: AsyncSession, running_contest: Contest, admin_user: User
) -> None:
    """Create a site token, email it, and never expose its digest."""
    site = await _make_site(session, running_contest, "Site A")
    admin_user.email = "admin@example.com"
    app, auth_service = _build_app(session)
    await session.commit()
    token = _actor_token(auth_service, username=admin_user.username, role=RoleEnum.ADMIN, contest_id=running_contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        created = await client.post(
            f"/c/{running_contest.login_slug}/admin/animator/secrets",
            data={"scope": site.id, "label": "Operator SP"},
        )
        listing = await client.get(f"/c/{running_contest.login_slug}/admin/animator/")

    _assert_partial(created)
    plaintext = _extract_secret(created)
    assert created.text.count(plaintext) == 1
    assert "A copy was emailed to admin@example.com." in created.text
    sent_email = app.state.email_service.provider.sent_emails[0]
    assert "Scope: Site: Site A" in str(sent_email["text_body"])
    assert "<td>Site A</td>" in created.text
    assert '<span class="badge' not in created.text
    assert digest_token(plaintext) not in created.text
    assert "secret_digest" not in created.text
    assert plaintext not in listing.text
    assert "Operator SP" in listing.text
    audit_rows = await list_recent_security_events(session, event_type=ADMIN_ACTION_EVENT_TYPE)
    assert len(audit_rows) == 1
    assert audit_rows[0].actor_label == admin_user.username
    assert audit_rows[0].metadata == {
        "action": "animator_credential_create",
        "target_type": "animator_operator_credential",
        "target_id": None,
        "detail": f"contest={running_contest.login_slug}; scope=site:{site.id}; label=Operator SP",
    }
    assert plaintext not in str(audit_rows[0].metadata)
    assert digest_token(plaintext) not in str(audit_rows[0].metadata)


@pytest.mark.asyncio
async def test_global_credential_works_without_sites_or_actor_email(
    session: AsyncSession, running_contest: Contest, admin_user: User
) -> None:
    """Create and list a global token without sites or an admin email."""
    app, auth_service = _build_app(session)
    await session.commit()
    token = _actor_token(auth_service, username=admin_user.username, role=RoleEnum.ADMIN, contest_id=running_contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        created = await client.post(
            f"/c/{running_contest.login_slug}/admin/animator/secrets",
            data={"scope": "global", "label": "Global control"},
        )

    _assert_partial(created)
    assert "Global control" in created.text
    assert "Global (all sites)" in created.text
    assert "<td>Global</td>" in created.text
    assert '<span class="badge' not in created.text
    assert "A copy was emailed to" not in created.text
    metas = await animator_access_service.list_site_secrets(session, running_contest.id)
    assert len(metas) == 1
    assert metas[0].site_id is None


@pytest.mark.asyncio
async def test_global_credential_email_identifies_global_scope(
    session: AsyncSession, running_contest: Contest, admin_user: User
) -> None:
    """Identify a global credential's scope in its emailed copy."""
    admin_user.email = "admin@example.com"
    app, auth_service = _build_app(session)
    await session.commit()
    token = _actor_token(auth_service, username=admin_user.username, role=RoleEnum.ADMIN, contest_id=running_contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.post(
            f"/c/{running_contest.login_slug}/admin/animator/secrets",
            data={"scope": "global", "label": "Global control"},
        )

    _assert_partial(response)
    sent_email = app.state.email_service.provider.sent_emails[0]
    assert "Scope: Global (all sites)" in str(sent_email["text_body"])


@pytest.mark.asyncio
async def test_create_errors_are_swappable_and_create_nothing(
    session: AsyncSession,
    running_contest: Contest,
    admin_user: User,
    uberadmin: UberAdmin,
) -> None:
    """Return HTTP 200 partials for every invalid create payload."""
    other = await _other_contest(session, uberadmin)
    foreign_site = await _make_site(session, other, "Foreign Site")
    app, auth_service = _build_app(session)
    await session.commit()
    token = _actor_token(auth_service, username=admin_user.username, role=RoleEnum.ADMIN, contest_id=running_contest.id)
    payloads = [
        {"scope": "global", "label": ""},
        {"scope": "global", "label": "x" * 201},
        {"label": "Missing scope"},
        {"scope": "unknown", "label": "Unknown scope"},
        {"scope": foreign_site.id, "label": "Foreign scope"},
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        for payload in payloads:
            response = await client.post(
                f"/c/{running_contest.login_slug}/admin/animator/secrets",
                data=payload,
            )
            _assert_partial(response)
            assert "alert-danger" in response.text

    assert await animator_access_service.list_site_secrets(session, running_contest.id) == []


@pytest.mark.asyncio
async def test_revoke_returns_partial_and_foreign_id_is_noop(
    session: AsyncSession,
    running_contest: Contest,
    admin_user: User,
    uberadmin: UberAdmin,
) -> None:
    """Swap the list after a revoke and reject a cross-contest secret inline."""
    token_plaintext = await animator_access_service.create_global_secret(
        session, contest_id=running_contest.id, label="Local"
    )
    other = await _other_contest(session, uberadmin)
    await animator_access_service.create_global_secret(session, contest_id=other.id, label="Foreign")
    local = await animator_access_service.list_site_secrets(session, running_contest.id)
    foreign = await animator_access_service.list_site_secrets(session, other.id)
    app, auth_service = _build_app(session)
    await session.commit()
    token = _actor_token(auth_service, username=admin_user.username, role=RoleEnum.ADMIN, contest_id=running_contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        revoked = await client.post(f"/c/{running_contest.login_slug}/admin/animator/secrets/{local[0].id}/revoke")
        rejected = await client.post(f"/c/{running_contest.login_slug}/admin/animator/secrets/{foreign[0].id}/revoke")
        listing = await client.get(f"/c/{running_contest.login_slug}/admin/animator/")

    _assert_partial(revoked)
    assert "Local" not in revoked.text
    assert token_plaintext not in revoked.text
    _assert_partial(rejected)
    assert "Credential not found." in rejected.text
    assert [meta.id for meta in await animator_access_service.list_site_secrets(session, other.id)] == [foreign[0].id]
    assert "Operator credential revoked." not in listing.text
    assert "Credential not found." not in listing.text
    audit_rows = await list_recent_security_events(session, event_type=ADMIN_ACTION_EVENT_TYPE)
    assert len(audit_rows) == 1
    assert audit_rows[0].actor_label == admin_user.username
    assert audit_rows[0].metadata == {
        "action": "animator_credential_revoke",
        "target_type": "animator_operator_credential",
        "target_id": local[0].id,
        "detail": f"contest={running_contest.login_slug}",
    }


@pytest.mark.asyncio
async def test_htmx_create_and_revoke_do_not_queue_flash(
    session: AsyncSession, running_contest: Contest, admin_user: User
) -> None:
    """Keep HTMX feedback out of the flash queue for later page loads."""
    app, auth_service = _build_app(session)
    await session.commit()
    token = _actor_token(auth_service, username=admin_user.username, role=RoleEnum.ADMIN, contest_id=running_contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        await client.post(
            f"/c/{running_contest.login_slug}/admin/animator/secrets",
            data={"scope": "global", "label": "Temporary"},
        )
        secrets = await animator_access_service.list_site_secrets(session, running_contest.id)
        await client.post(f"/c/{running_contest.login_slug}/admin/animator/secrets/{secrets[0].id}/revoke")
        page = await client.get(f"/c/{running_contest.login_slug}/admin/animator/")

    assert "New operator credential" not in page.text
    assert "Operator credential revoked." not in page.text
    assert "Credential not found." not in page.text


@pytest.mark.asyncio
async def test_disabled_state_rendered(session: AsyncSession, running_contest: Contest, admin_user: User) -> None:
    """Render an unchecked access toggle when animator access is disabled."""
    running_contest.animator_enabled = False
    app, auth_service = _build_app(session)
    await session.commit()
    token = _actor_token(auth_service, username=admin_user.username, role=RoleEnum.ADMIN, contest_id=running_contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        page = await client.get(f"/c/{running_contest.login_slug}/admin/animator/")
    assert page.status_code == 200
    assert 'name="animator_enabled"' in page.text
    assert "checked" not in page.text.split('name="animator_enabled"')[1].split(">", maxsplit=1)[0]
