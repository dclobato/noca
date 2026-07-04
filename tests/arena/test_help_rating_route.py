#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Smoke test for the Arena difficulty/rating help page.

Renders ``/help/rating`` against the real template through the actual help
router so a Jinja/KaTeX typo or a missing template variable is caught. Exercised
as a logged-out guest, since the page is on the Arena public allowlist.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.routes.help import router as arena_help_router
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from arena.services.token_service import ArenaTokenAction
from shared.services.arena_difficulty_histogram import BIN_COUNT, persist_difficulty_histogram

TEST_JWT_SECRET = "test-secret-key-for-help-rating-tests-32b!!"

# Named routes that ``_base.html`` resolves via ``url_for`` for a guest render.
# The help router itself supplies ``arena_help_rating`` / ``arena_help_languages``,
# and the avatar/presence endpoints live behind ``{% if current_user %}`` so a
# logged-out render never reaches them.
_NAV_ROUTE_NAMES = (
    "arena_admin_affiliation_list",
    "arena_admin_category_list",
    "arena_admin_dashboard",
    "arena_admin_dashboard_ai_usage",
    "arena_admin_dashboard_login_history",
    "arena_admin_dashboard_security_events",
    "arena_admin_dashboard_service_status",
    "arena_admin_dashboard_submissions",
    "arena_admin_problem_list",
    "arena_admin_user_list",
    "arena_classes_index",
    "arena_classes_manage",
    "arena_classes_open",
    "arena_classes_registered",
    "arena_dashboard",
    "arena_login",
    "arena_logout",
    "arena_notifications_list",
    "arena_problem_list",
    "arena_ranking_affiliations",
    "arena_ranking_index",
    "arena_ranking_users",
    "arena_signup",
    "arena_status",
    "arena_user_profile",
)


def _build_help_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app that can render the real help_rating template."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    arena_dir = Path(__file__).resolve().parents[2] / "arena"
    templates = Jinja2Templates(directory=arena_dir / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["next_rating_update_text"] = lambda request: None
    templates.env.globals["arena_role_labels"] = ARENA_ROLE_DISPLAY
    setup_flash(templates)
    app.state.arena_templates = templates
    app.state.arena_db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.jwt_service = JWTService(
        config=load_token_config_from_dict(
            {
                "SECRET_KEY": TEST_JWT_SECRET,
                "JWTSERVICE_ALGORITHM": "HS256",
                "JWTSERVICE_ISSUER": "noca-arena-test",
            }
        ),
        logger=logging.getLogger(__name__),
        action_enum=ArenaTokenAction,
    )

    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")
    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")

    for name in _NAV_ROUTE_NAMES:

        async def _stub() -> Response:
            return Response("stub")

        app.add_api_route(f"/__stub__/{name}", _stub, name=name)

    app.include_router(arena_help_router)
    return app


@pytest.mark.asyncio
async def test_help_rating_renders_for_guest(session: AsyncSession) -> None:
    """The rating help page renders (200) and documents the per-problem pivot ramp."""
    app = _build_help_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/help/rating", headers={"Accept": "text/html"})

    assert response.status_code == 200
    body = response.text
    # Step 4 must describe the attempt-gated effective pivot introduced by Option 3.
    assert "m_{\\text{eff}}" in body
    assert "N_p" in body
    # The blend scale constant is surfaced from the rating service into the page.
    assert "pivot" in body.lower()


@pytest.mark.asyncio
async def test_difficulty_distribution_empty_before_first_cycle(session: AsyncSession) -> None:
    """The JSON endpoint returns an explicit empty shape before any rating cycle runs."""
    app = _build_help_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/help/rating/difficulty-distribution")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {"counts": [], "total_problems": 0, "computed_at": None}


@pytest.mark.asyncio
async def test_difficulty_distribution_returns_latest_snapshot(session: AsyncSession) -> None:
    """The JSON endpoint surfaces the persisted histogram snapshot once one exists."""
    await persist_difficulty_histogram(session, [1, 55, 100], datetime.now(UTC))
    await session.commit()

    app = _build_help_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/help/rating/difficulty-distribution")

    assert response.status_code == 200
    payload = response.json()
    assert payload["bins"] == BIN_COUNT
    assert payload["total_problems"] == 3
    assert sum(payload["counts"]) == 3
    assert payload["computed_at"] is not None
