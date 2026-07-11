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
from web.routes.profile import router as profile_router
from web.services.authentication_service import AuthAction, AuthenticationService

TEST_JWT_SECRET = "test-secret-key-for-tests-only-32bytes"


class _NoopGeo:
    def get_details_by_ip(self, ip_address: str | None) -> GeolocationDetails | None:
        return None


def _build_app(session: AsyncSession) -> FastAPI:
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
    return app


@pytest.mark.asyncio
async def test_htmx_auth_failure_uses_hx_redirect(session: AsyncSession) -> None:
    app = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/profile", headers={"HX-Request": "true"}, follow_redirects=False)

    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/login"
    assert "location" not in response.headers


@pytest.mark.asyncio
async def test_non_htmx_auth_failure_keeps_http_redirect(session: AsyncSession) -> None:
    app = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/profile", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"
