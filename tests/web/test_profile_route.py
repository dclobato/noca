import logging
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from shared.enumerations import RoleEnum
from shared.services.geolocation import GeolocationDetails, GeolocationIP
from web.models.site import Site
from web.models.users import UberAdmin
from web.routes.profile import router as profile_router
from web.services.authentication_service import AuthAction, AuthenticationService
from web.services.site_service import normalize_site_name_key

TEST_JWT_SECRET = "test-secret-key-for-tests-only-32bytes"


class _NoopGeo:
    def get_details_by_ip(self, ip_address: str | None) -> GeolocationDetails | None:
        return None


def _build_profile_app(session: AsyncSession) -> tuple[FastAPI, AuthenticationService]:
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
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)

    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/css", StaticFiles(directory=web_dir / "static" / "css"), name="static_css")
    app.mount("/static/js", StaticFiles(directory=web_dir / "static" / "js"), name="static_js")
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")

    @app.get("/uberadmin", name="uberadmin_dashboard")
    async def _uberadmin_dashboard() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/logout", name="logout")
    async def _logout() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/login", name="login_get")
    async def _login() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/c/{slug}", name="contest_dashboard")
    async def _contest_dashboard(slug: str) -> dict[str, str]:
        return {"slug": slug}

    @app.get("/c/{slug}/clock", name="contest_clock")
    async def _contest_clock(slug: str) -> dict[str, str]:
        return {"slug": slug}

    app.include_router(profile_router)
    return app, app.state.auth_service


async def _uberadmin_token(auth_service: AuthenticationService, session: AsyncSession, username: str) -> str:
    return await auth_service.uberadmin_login(username=username, password="TestPass1!", session=session)


async def _contest_user_token(
    auth_service: AuthenticationService,
    session: AsyncSession,
    username: str,
    contest_id: str,
) -> str:
    return await auth_service.user_login(
        username=username,
        password="TestPass1!",
        contest_id=contest_id,
        session=session,
    )


@pytest.mark.asyncio
async def test_uberadmin_profile_renders_without_photo_controls(session: AsyncSession, uberadmin: UberAdmin) -> None:
    app, auth_service = _build_profile_app(session)
    token = await _uberadmin_token(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/profile")

    assert response.status_code == 200
    assert 'card-header fw-semibold">Profile' in response.text
    assert "Change Password" in response.text
    assert 'card-header fw-semibold">Profile Photo' not in response.text
    assert "Back to dashboard" in response.text
    assert "/profile/email" in response.text


@pytest.mark.asyncio
async def test_uberadmin_profile_fullname_update_reuses_shared_validation(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    app, auth_service = _build_profile_app(session)
    token = await _uberadmin_token(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        success = await client.post(
            "/profile/fullname",
            data={"fullname": "Updated UberAdmin"},
            follow_redirects=False,
        )
        invalid = await client.post(
            "/profile/fullname",
            data={"fullname": "   "},
            follow_redirects=False,
        )

    await session.refresh(uberadmin)
    assert success.status_code == 303
    assert success.headers["location"] == "/profile"
    assert uberadmin.fullname == "Updated UberAdmin"
    assert invalid.status_code == 422
    assert "Display name cannot be empty." in invalid.text


@pytest.mark.asyncio
async def test_uberadmin_profile_password_update_reuses_shared_validation(
    session: AsyncSession, uberadmin: UberAdmin
) -> None:
    app, auth_service = _build_profile_app(session)
    token = await _uberadmin_token(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        wrong_current = await client.post(
            "/profile/password",
            data={
                "current_password": "wrong",
                "new_password": "BetterPass2!",
                "confirm_password": "BetterPass2!",
            },
            follow_redirects=False,
        )
        mismatch = await client.post(
            "/profile/password",
            data={
                "current_password": "TestPass1!",
                "new_password": "BetterPass2!",
                "confirm_password": "MismatchPass2!",
            },
            follow_redirects=False,
        )
        success = await client.post(
            "/profile/password",
            data={
                "current_password": "TestPass1!",
                "new_password": "BetterPass2!",
                "confirm_password": "BetterPass2!",
            },
            follow_redirects=False,
        )

    await session.refresh(uberadmin)
    assert wrong_current.status_code == 422
    assert "Current password is incorrect." in wrong_current.text
    assert mismatch.status_code == 422
    assert "New passwords do not match." in mismatch.text
    assert success.status_code == 303
    assert success.headers["location"] == "/profile"

    second_token = await auth_service.uberadmin_login(
        username=uberadmin.username,
        password="BetterPass2!",
        session=session,
    )
    assert isinstance(second_token, str)


@pytest.mark.asyncio
async def test_contest_user_profile_shows_site_as_read_only_information(
    session: AsyncSession, running_contest, uberadmin, team_user
) -> None:
    site = Site(
        sitename="Campus A",
        sitename_normalized=normalize_site_name_key("Campus A"),
        contest_id=running_contest.id,
    )
    session.add(site)
    await session.flush()
    team_user.site_id = site.id
    await session.commit()

    app, auth_service = _build_profile_app(session)
    token = await _contest_user_token(auth_service, session, team_user.username, running_contest.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get("/profile")

    assert response.status_code == 200
    assert 'name="site_id"' not in response.text
    assert "Managed by contest administrators." in response.text
    assert "Campus A" in response.text


@pytest.mark.asyncio
async def test_uberadmin_profile_email_update_validation(session: AsyncSession, uberadmin: UberAdmin) -> None:
    app, auth_service = _build_profile_app(session)
    token = await _uberadmin_token(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        invalid = await client.post(
            "/profile/email",
            data={"email": "invalid-email"},
            follow_redirects=False,
        )
        success = await client.post(
            "/profile/email",
            data={"email": "updated.admin@example.com"},
            follow_redirects=False,
        )

    await session.refresh(uberadmin)
    assert invalid.status_code == 422
    assert "Invalid email address." in invalid.text
    assert success.status_code == 303
    assert success.headers["location"] == "/profile"
    assert uberadmin.email_normalizado == "updated.admin@example.com"


@pytest.mark.asyncio
async def test_contest_user_profile_email_can_be_set_and_cleared(
    session: AsyncSession, running_contest, team_user
) -> None:
    app, auth_service = _build_profile_app(session)
    token = await _contest_user_token(auth_service, session, team_user.username, running_contest.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        set_email = await client.post(
            "/profile/email",
            data={"email": "team.profile@example.com"},
            follow_redirects=False,
        )
        clear_email = await client.post(
            "/profile/email",
            data={"email": ""},
            follow_redirects=False,
        )

    await session.refresh(team_user)
    assert set_email.status_code == 303
    assert clear_email.status_code == 303
    assert team_user.email_normalizado is None
