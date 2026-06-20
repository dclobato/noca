from __future__ import annotations

import logging
from datetime import UTC, datetime
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
from shared.services.geolocation import GeolocationIP
from web.routes.auth import router as auth_router
from web.routes.generaluser_dashboard import router as contest_dashboard_router
from web.routes.root import router as root_router
from web.routes.uberadmin_dashboard import router as uberadmin_dashboard_router
from web.services.authentication_service import AuthAction, AuthenticationService

TEST_JWT_SECRET = "test-secret-key-for-tests-only-32bytes"


class _NoopGeo:
    def get_location_by_ip(self, ip_address: str | None) -> str | None:
        return None


def _build_app(session: AsyncSession) -> tuple[FastAPI, AuthenticationService]:
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
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")

    @app.get("/profile", name="profile_get")
    async def _profile() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/problems", name="problem_bank")
    async def _problem_bank() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/uberadmin/uberadmins", name="list_uberadmins_route")
    async def _list_uberadmins() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/c/{slug}/admin", name="view")
    async def _contest_admin(slug: str) -> dict[str, str]:
        return {"slug": slug}

    app.include_router(auth_router)
    app.include_router(root_router)
    app.include_router(uberadmin_dashboard_router)
    app.include_router(contest_dashboard_router)
    return app, app.state.auth_service


async def _login_uberadmin(
    auth_service: AuthenticationService,
    session: AsyncSession,
    username: str,
) -> str:
    return await auth_service.uberadmin_login(username=username, password="TestPass1!", session=session)


def _contest_token(auth_service: AuthenticationService, *, username: str, contest_id: str) -> str:
    return auth_service.jwt_service.create(
        action=AuthAction.WEB_ACCESS,
        sub=username,
        audience=RoleEnum.TEAM.value,
        extra_data={"contest_id": contest_id, "session_started_at": int(datetime.now(UTC).timestamp())},
    )


@pytest.mark.asyncio
async def test_uberadmin_can_deactivate_past_contest_and_view_inactive_list(
    session: AsyncSession,
    stopped_contest,
    uberadmin,
) -> None:
    app, auth_service = _build_app(session)
    token = await _login_uberadmin(auth_service, session, uberadmin.username)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        dashboard = await client.get("/uberadmin/")
        response = await client.post(
            f"/uberadmin/contests/{stopped_contest.id}/deactivate",
            follow_redirects=False,
        )
        inactive = await client.get("/uberadmin/contests/inactive")

    assert dashboard.status_code == 200
    assert "Make inactive" in dashboard.text
    assert response.status_code == 303
    assert response.headers["location"] == "http://testserver/uberadmin/"
    assert inactive.status_code == 200
    assert "Stopped Contest" in inactive.text


@pytest.mark.asyncio
async def test_public_contests_and_contest_access_hide_inactive_contest(
    session: AsyncSession,
    stopped_contest,
    team_user,
    uberadmin,
) -> None:
    stopped_contest.active = False
    await session.commit()
    app, auth_service = _build_app(session)
    token = _contest_token(auth_service, username=team_user.username, contest_id=stopped_contest.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        contests = await client.get("/contests")
        login_get = await client.get(f"/c/{stopped_contest.login_slug}/login")
        login_post = await client.post(
            f"/c/{stopped_contest.login_slug}/login",
            data={"identifier": team_user.username, "password": "TestPass1!"},
        )
        dashboard = await client.get(f"/c/{stopped_contest.login_slug}/")

    assert contests.status_code == 200
    assert "Stopped Contest" not in contests.text
    assert login_get.status_code == 404
    assert login_post.status_code == 404
    assert dashboard.status_code == 404
