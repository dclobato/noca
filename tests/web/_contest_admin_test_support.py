#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared boilerplate for the contest-admin dashboard route tests.

`test_contest_admin_scoreboard_release.py` and `test_contest_admin_problem_set_release.py`
each build the smallest FastAPI app that can render the contest-admin dashboard
and exercise one of its POST-action cards. The app wiring (templates, static
mounts, auth service, stub routes) is identical between them; only the set of
routers under test and the extra dashboard-linked stub routes differ. This
module holds that common part once.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from shared.enumerations import RoleEnum
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.services.authentication_service import AuthAction, AuthenticationService
from web.template_globals import register_template_globals

TEST_JWT_SECRET = "test-secret-key-for-tests-only-32bytes"

_DEFAULT_SCOPED_STUB_NAMES = (
    "manage_users",
    "manage_problems",
    "import_export",
    "users_per_site_report",
    "contest_admin_counters",
    "animator_settings",
    "contest_dashboard",
    "contest_clock",
)
_GLOBAL_STUB_NAMES = ("uberadmin_dashboard", "profile_get", "logout")


class _NoopGeo:
    """Test geolocation service that never resolves an address."""

    def get_details_by_ip(self, ip_address: str | None):  # noqa: ANN201 - test stub
        """Return no geolocation details for any address."""
        return None


async def _stub_scoped(slug: str) -> dict[str, str]:
    """Stand in for a contest-scoped route the dashboard template links to."""
    return {"slug": slug}


async def _stub_global() -> dict[str, str]:
    """Stand in for a global route the base template links to."""
    return {"ok": "ok"}


def build_contest_admin_app(
    session: AsyncSession,
    *,
    routers: tuple[APIRouter, ...],
    extra_scoped_stub_names: tuple[str, ...] = (),
) -> tuple[FastAPI, AuthenticationService]:
    """Build the smallest application that exercises the given contest-admin routers.

    `routers` are the routers under test (e.g. `contest_admin_router`, and
    `contest_admin_metadata_router` when the dashboard being rendered links to
    the metadata page). `extra_scoped_stub_names` adds route names, beyond the
    common set every dashboard render needs, that a specific test module's
    dashboard template also resolves through `url_for`.
    """
    from shared.services.geolocation import GeolocationIP

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    web_dir = Path(__file__).resolve().parents[2] / "web"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=web_dir / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["brand_name"] = "NOCA"
    register_template_globals(templates)
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
    app.mount("/static/shared/js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")

    # Route stubs the dashboard template resolves through url_for. The cards
    # under test are built from the routers under test themselves.
    for name in (*_DEFAULT_SCOPED_STUB_NAMES, *extra_scoped_stub_names):
        app.add_api_route(f"/stub/{name}/{{slug}}", _stub_scoped, name=name, methods=["GET"])
    for name in _GLOBAL_STUB_NAMES:
        app.add_api_route(f"/stub/{name}", _stub_global, name=name, methods=["GET"])

    for router in routers:
        app.include_router(router)
    return app, app.state.auth_service


def actor_token(auth_service: AuthenticationService, *, username: str, contest_id: str) -> str:
    """Create a contest-scoped admin access token."""
    return auth_service.jwt_service.create(
        action=AuthAction.WEB_ACCESS,
        sub=username,
        audience=RoleEnum.ADMIN.value,
        extra_data={"contest_id": contest_id, "session_started_at": int(datetime.now(UTC).timestamp())},
    )


async def admin_on(session: AsyncSession, contest: Contest, uberadmin: UberAdmin, username: str) -> User:
    """Persist a contest administrator for the given contest."""
    user = User(
        username=username,
        fullname="Contest Admin",
        role=RoleEnum.ADMIN,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


async def dashboard_html(
    session: AsyncSession,
    contest: Contest,
    admin: User,
    *,
    routers: tuple[APIRouter, ...],
    extra_scoped_stub_names: tuple[str, ...] = (),
) -> str:
    """Render the contest-admin dashboard as the given administrator."""
    app, auth_service = build_contest_admin_app(
        session, routers=routers, extra_scoped_stub_names=extra_scoped_stub_names
    )
    await session.commit()
    token = actor_token(auth_service, username=admin.username, contest_id=contest.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("noca_access_token", token)
        response = await client.get(f"/c/{contest.login_slug}/admin/")
    assert response.status_code == 200
    return response.text
