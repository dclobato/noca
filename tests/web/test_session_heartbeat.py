#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Web session keepalive: the request an open page makes so its session survives.

Web sessions slide, but rotation only happens on a request that arrives inside
the token's half-life window. A page left open makes none, so a long edit used to
end at the login page -- and since the expired-session redirect turns a ``POST``
into a ``GET``, the form body went with it. These tests pin the request that closes that
gap and the configuration that decides when the browser sends it.
"""

from __future__ import annotations

import logging
from http.cookies import SimpleCookie
from pathlib import Path
from time import time
from typing import cast

import pytest
from fastapi import Depends, FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import NoMatchFound

from shared.enumerations import RoleEnum
from shared.services.geolocation import GeolocationDetails, GeolocationIP
from web.config import settings
from web.dependencies import enforce_web_default_auth
from web.main import app as web_app
from web.middleware.auth_token_refresh import AuthTokenRefreshMiddleware
from web.routes.session import router as session_router
from web.services.authentication_service import AuthAction, AuthenticationService
from web.template_globals import session_heartbeat_config, session_heartbeat_seconds, template_globals

TEST_JWT_SECRET = "test-secret-key-for-tests-only-32bytes"
AUTH_COOKIE = "noca_access_token"


class _NoopGeo:
    """Geolocation stand-in: the keepalive records no login history."""

    def get_details_by_ip(self, ip_address: str | None) -> GeolocationDetails | None:
        return None


def _build_app() -> tuple[FastAPI, AuthenticationService]:
    """Build the smallest app that reproduces the production keepalive path.

    The route carries no authentication dependency of its own by design, so the
    app-wide ``enforce_web_default_auth`` dependency and the refresh middleware
    are exactly what has to be present for the rotation to happen.
    """
    app = FastAPI(dependencies=[Depends(enforce_web_default_auth)])
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    app.add_middleware(AuthTokenRefreshMiddleware)

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
    app.include_router(session_router)

    @app.get("/login", name="login_get")
    async def _login() -> dict[str, str]:
        return {"page": "login"}

    return app, app.state.auth_service


def _token(auth_service: AuthenticationService, *, expires_in: int) -> str:
    """Return a contest-scoped access token expiring in `expires_in` seconds."""
    return auth_service.jwt_service.create(
        action=AuthAction.WEB_ACCESS,
        sub="team01",
        audience=RoleEnum.TEAM.value,
        extra_data={"contest_id": "contest-1", "session_started_at": int(time())},
        expires_in=expires_in,
    )


def _refreshed_cookie(response) -> str | None:
    """Return the auth cookie the response set, when it set one."""
    jar = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        jar.load(header)
    morsel = jar.get(AUTH_COOKIE)
    return morsel.value if morsel else None


def _request(app: FastAPI, **state: object) -> Request:
    """Build a minimal request bound to `app`, enough for `url_for` and state reads."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("testserver", 80),
            "path": "/",
            "query_string": b"",
            "headers": [],
            "app": app,
            "router": app.router,
            "state": dict(state),
        }
    )


class _Validation:
    """Stand-in for the middleware's cached validation result."""

    def __init__(self, *, valid: bool) -> None:
        self.valid = valid


class TestSessionHeartbeatRoute:
    """The keepalive request itself."""

    @pytest.mark.asyncio
    async def test_heartbeat_rotates_a_session_inside_the_refresh_window(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The regression test: a ping from an open page renews the cookie.

        Without it the session simply expires under a user who is typing, and
        the Save that follows is answered with a redirect that discards the form.
        """
        monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 3600)
        app, auth_service = _build_app()
        token = _token(auth_service, expires_in=600)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            cookies={AUTH_COOKIE: token},
        ) as client:
            response = await client.post("/session/heartbeat")

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        refreshed = _refreshed_cookie(response)
        assert refreshed is not None
        assert refreshed != token

        renewed = auth_service.jwt_service.validate(refreshed)
        assert renewed.valid
        assert renewed.expires_in > 600

    @pytest.mark.asyncio
    async def test_heartbeat_leaves_a_token_outside_the_window_alone(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A ping early in a token's life issues no cookie, so pings stay cheap."""
        monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 3600)
        app, auth_service = _build_app()
        token = _token(auth_service, expires_in=3500)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            cookies={AUTH_COOKIE: token},
        ) as client:
            response = await client.post("/session/heartbeat")

        assert response.status_code == 200
        assert _refreshed_cookie(response) is None

    @pytest.mark.asyncio
    async def test_heartbeat_is_not_public(self) -> None:
        """An anonymous ping must not reach the endpoint or extend anything.

        The redirect is a `302`, which browsers turn into a `GET` for a `POST`
        just as a `303` does -- which is the whole reason an expired session
        costs the user their form.
        """
        app, _ = _build_app()

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post("/session/heartbeat", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"].endswith("/login")
        assert _refreshed_cookie(response) is None


class TestSessionHeartbeatConfig:
    """What `_base.html` reads to decide whether, and how often, to ping."""

    def test_a_live_session_is_configured(self) -> None:
        """An authenticated page carries the keepalive URL and its interval."""
        app, _ = _build_app()

        config = session_heartbeat_config(_request(app, validated_token=_Validation(valid=True)))

        assert config is not None
        assert str(config["heartbeat_url"]).endswith("/session/heartbeat")
        assert config["interval_seconds"] == session_heartbeat_seconds()

    def test_an_anonymous_page_is_not_configured(self) -> None:
        """Login and the other public pages have no session to keep alive."""
        app, _ = _build_app()

        assert session_heartbeat_config(_request(app)) is None
        assert session_heartbeat_config(_request(app, validated_token=_Validation(valid=False))) is None

    def test_a_capped_out_session_is_not_configured(self) -> None:
        """Past the absolute cap the session is over; pinging would only mislead.

        The token still validates -- the cap is enforced beside it, not inside it --
        so this has to be checked separately or a finished session would keep
        rendering a keepalive that can never rotate anything.
        """
        app, _ = _build_app()

        config = session_heartbeat_config(
            _request(app, validated_token=_Validation(valid=True), token_cap_exceeded=True)
        )

        assert config is None

    def test_the_interval_lands_strictly_inside_the_refresh_window(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A ping outside the half-life window rotates nothing, so the margin is the point."""
        monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 3600)

        interval = session_heartbeat_seconds()

        assert interval < settings.JWT_EXPIRE_SECONDS // 2
        assert interval >= 60

    def test_a_very_short_token_lifetime_keeps_the_floor(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The floor bounds request volume; such a configuration is already unusable."""
        monkeypatch.setattr(settings, "JWT_EXPIRE_SECONDS", 60)

        assert session_heartbeat_seconds() == 60

    def test_the_real_app_registers_every_route_the_keepalive_block_resolves(self) -> None:
        """`_base.html` resolves both names unguarded on every authenticated page.

        The guarantee is `web/main.py`'s unconditional `include_router`, and this
        test is what keeps it from silently becoming conditional -- otherwise
        every open page would quietly lose its session keepalive again.
        """
        assert web_app.url_path_for("web_session_heartbeat")
        assert web_app.url_path_for("static_shared_js", path="noca-presence.js")

    def test_config_raises_when_the_keepalive_route_is_absent(self) -> None:
        """An application missing the route must fail loudly, not render without it."""
        bare = FastAPI()

        with pytest.raises(NoMatchFound):
            session_heartbeat_config(_request(bare, validated_token=_Validation(valid=True)))


def _build_render_app() -> tuple[FastAPI, AuthenticationService]:
    """Extend the keepalive app with the templates needed to render a real page.

    ``_base.html`` resolves the four static mounts directly and reaches every
    other destination through ``nav_url``, which is already tolerant of routes a
    single-router application does not mount.
    """
    app, auth_service = _build_app()
    web_dir = Path(__file__).resolve().parents[2] / "web"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"

    templates = Jinja2Templates(directory=web_dir / "template")
    templates.env.globals.update(
        {"app_version": "test", "brand_name": "NOCA", "healthmon_url": "", **template_globals()}
    )
    setup_flash(templates)
    app.state.templates = templates

    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/css", StaticFiles(directory=web_dir / "static" / "css"), name="static_css")
    app.mount("/static/js", StaticFiles(directory=web_dir / "static" / "js"), name="static_js")
    app.mount("/static/shared/js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")

    @app.get("/rendered", name="rendered_page")
    async def _rendered(request: Request) -> Response:
        return templates.TemplateResponse(request, "_base.html", {})

    return app, auth_service


class TestKeepaliveRendersOnAnAuthenticatedPage:
    """The template binding itself, which the configuration tests cannot reach.

    Every test above calls ``session_heartbeat_config`` directly. That pins the
    configuration but says nothing about ``_base.html`` emitting it, so deleting
    the block -- or renaming the variable it sets -- would leave every page
    without a keepalive and no failing test. These render a real page instead.
    """

    @pytest.mark.asyncio
    async def test_a_live_session_page_carries_the_heartbeat_element(self) -> None:
        """An authenticated page ships the config element and the shared script."""
        app, auth_service = _build_render_app()
        token = _token(auth_service, expires_in=settings.JWT_EXPIRE_SECONDS)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver", cookies={AUTH_COOKIE: token}
        ) as client:
            response = await client.get("/rendered")

        assert response.status_code == 200
        assert "data-noca-presence" in response.text
        assert 'data-heartbeat-url="http://testserver/session/heartbeat"' in response.text
        assert f'data-interval-seconds="{session_heartbeat_seconds()}"' in response.text
        # Web has no presence domain, so the shared script's green-dot half must
        # stay off while its keepalive half runs.
        assert 'data-presence-enabled="false"' in response.text
        assert "noca-presence.js" in response.text

    @pytest.mark.asyncio
    async def test_an_anonymous_page_carries_no_heartbeat_element(self) -> None:
        """With no session there is nothing to keep alive, and nothing is emitted."""
        app, _ = _build_render_app()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get("/rendered", follow_redirects=False)

        assert "data-noca-presence" not in response.text
