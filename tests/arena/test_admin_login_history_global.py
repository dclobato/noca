#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the global Arena login history admin page: service and route layers."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_auth_records  # noqa: F401 — register ORM mapper
import arena.models.arena_users  # noqa: F401 — register ORM mapper
from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_users import ArenaUser
from arena.routes.admin_dashboard_history import router
from arena.services import admin_login_history_service
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from shared.db_schema.arena.arena_users import arena_login_history
from shared.enumerations import VERDICT_BADGE_CLASSES, VERDICT_LABELS, ArenaRole

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _make_user(
    session: AsyncSession,
    *,
    nome: str = "Test User",
    email: str | None = None,
) -> ArenaUser:
    """Insert and return a minimal ArenaUser."""
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome=nome,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        session_version=0,
        precisa_trocar_senha=False,
        usa_2fa=False,
        com_foto=False,
        ai_backend_credits=0,
        user_rating=0,
        solved_problems=0,
    )
    user.email = email or f"user_{uuid.uuid4().hex[:6]}@test.example"
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


async def _insert_login(
    session: AsyncSession,
    user_id: str,
    *,
    dta_login: datetime | None = None,
    location: str | None = "São Paulo, BR",
    ip_address: str | None = "1.2.3.4",
    mode: str | None = "password",
) -> None:
    """Insert a login history record for the given user."""
    await session.execute(
        insert(arena_login_history).values(
            arena_user_id=user_id,
            dta_login=dta_login or datetime.now(UTC),
            ip_address=ip_address,
            location=location,
            user_agent="Mozilla/5.0",
            mode=mode,
        )
    )


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_history_empty_returns_zero_total(session: AsyncSession) -> None:
    """No login records → empty page with total=0."""
    result = await admin_login_history_service.list_global_login_history_paginated(session, page=1, per_page=25)
    assert result.total == 0
    assert result.items == []


@pytest.mark.asyncio
async def test_global_history_returns_records_across_all_users(session: AsyncSession) -> None:
    """Records from multiple users are all returned when no search is applied."""
    alice = await _make_user(session, nome="Alice")
    bob = await _make_user(session, nome="Bob")
    await _insert_login(session, alice.id)
    await _insert_login(session, bob.id)
    await session.flush()

    result = await admin_login_history_service.list_global_login_history_paginated(session, page=1, per_page=25)
    assert result.total == 2


@pytest.mark.asyncio
async def test_global_history_search_by_user_name(session: AsyncSession) -> None:
    """Search by user name is case-insensitive and partial."""
    alice = await _make_user(session, nome="Alice Wonderland")
    bob = await _make_user(session, nome="Bob Builder")
    await _insert_login(session, alice.id)
    await _insert_login(session, bob.id)
    await session.flush()

    result = await admin_login_history_service.list_global_login_history_paginated(
        session, page=1, per_page=25, search="alice"
    )
    assert result.total == 1
    assert result.items[0].arena_user_id == alice.id


@pytest.mark.asyncio
async def test_global_history_search_by_email(session: AsyncSession) -> None:
    """Search matches against normalised email address."""
    user = await _make_user(session, email="findme@test.example")
    await _insert_login(session, user.id)
    await session.flush()

    result = await admin_login_history_service.list_global_login_history_paginated(
        session, page=1, per_page=25, search="FINDME"
    )
    assert result.total == 1


@pytest.mark.asyncio
async def test_global_history_search_by_location(session: AsyncSession) -> None:
    """Search matches against the login location field."""
    user_sp = await _make_user(session, nome="São Paulo User")
    user_rj = await _make_user(session, nome="Rio User")
    await _insert_login(session, user_sp.id, location="São Paulo, BR")
    await _insert_login(session, user_rj.id, location="Rio de Janeiro, BR")
    await session.flush()

    result = await admin_login_history_service.list_global_login_history_paginated(
        session, page=1, per_page=25, search="são paulo"
    )
    assert result.total == 1
    assert result.items[0].arena_user_id == user_sp.id


@pytest.mark.asyncio
async def test_global_history_date_from_excludes_earlier_records(session: AsyncSession) -> None:
    """date_from_utc is inclusive; records strictly before it are excluded."""
    user = await _make_user(session)
    base = datetime(2026, 3, 10, tzinfo=UTC)
    await _insert_login(session, user.id, dta_login=base - timedelta(days=1))
    await _insert_login(session, user.id, dta_login=base)
    await session.flush()

    result = await admin_login_history_service.list_global_login_history_paginated(
        session, page=1, per_page=25, date_from_utc=base
    )
    assert result.total == 1
    assert result.items[0].dta_login.date() == base.date()


@pytest.mark.asyncio
async def test_global_history_date_to_is_exclusive_upper_bound(session: AsyncSession) -> None:
    """date_to_utc is an exclusive upper bound; records on or after it are excluded."""
    user = await _make_user(session)
    boundary = datetime(2026, 3, 11, tzinfo=UTC)
    await _insert_login(session, user.id, dta_login=boundary - timedelta(hours=1))
    await _insert_login(session, user.id, dta_login=boundary)
    await session.flush()

    result = await admin_login_history_service.list_global_login_history_paginated(
        session, page=1, per_page=25, date_to_utc=boundary
    )
    assert result.total == 1


@pytest.mark.asyncio
async def test_global_history_date_range_combined(session: AsyncSession) -> None:
    """Both date_from and date_to together constrain results to the window."""
    user = await _make_user(session)
    for day in (8, 9, 10, 11, 12):
        await _insert_login(session, user.id, dta_login=datetime(2026, 3, day, tzinfo=UTC))
    await session.flush()

    result = await admin_login_history_service.list_global_login_history_paginated(
        session,
        page=1,
        per_page=25,
        date_from_utc=datetime(2026, 3, 9, tzinfo=UTC),
        date_to_utc=datetime(2026, 3, 12, tzinfo=UTC),
    )
    assert result.total == 3


@pytest.mark.asyncio
async def test_global_history_sort_asc_returns_oldest_first(session: AsyncSession) -> None:
    """sort_dir='asc' returns records from oldest to newest."""
    user = await _make_user(session)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    await _insert_login(session, user.id, dta_login=base)
    await _insert_login(session, user.id, dta_login=base + timedelta(hours=2))
    await session.flush()

    result = await admin_login_history_service.list_global_login_history_paginated(
        session, page=1, per_page=25, sort_dir="asc"
    )
    assert result.items[0].dta_login <= result.items[1].dta_login


@pytest.mark.asyncio
async def test_global_history_sort_desc_returns_newest_first(session: AsyncSession) -> None:
    """sort_dir='desc' (default) returns records from newest to oldest."""
    user = await _make_user(session)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    await _insert_login(session, user.id, dta_login=base)
    await _insert_login(session, user.id, dta_login=base + timedelta(hours=2))
    await session.flush()

    result = await admin_login_history_service.list_global_login_history_paginated(
        session, page=1, per_page=25, sort_dir="desc"
    )
    assert result.items[0].dta_login >= result.items[1].dta_login


@pytest.mark.asyncio
async def test_global_history_second_page_returns_correct_slice(session: AsyncSession) -> None:
    """Second page returns remaining records; total reflects full count."""
    user = await _make_user(session)
    for i in range(5):
        await _insert_login(session, user.id, dta_login=datetime(2026, 1, i + 1, tzinfo=UTC))
    await session.flush()

    result = await admin_login_history_service.list_global_login_history_paginated(session, page=2, per_page=3)
    assert result.total == 5
    assert result.page == 2
    assert len(result.items) == 2


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------


def _build_app(session: Any, *, authorized: bool = True) -> FastAPI:
    """Build a minimal FastAPI app for the global login history route tests."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret")

    arena_dir = Path(__file__).resolve().parents[2] / "arena"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=arena_dir / "template")
    templates.env.globals["arena_format_datetime"] = lambda value, user, fmt=None: str(value)
    templates.env.globals["arena_format_relative_datetime"] = lambda value: "just now"
    templates.env.globals["app_version"] = "test"
    templates.env.globals["next_rating_update_text"] = lambda request: None
    templates.env.globals["verdict_badge_classes"] = VERDICT_BADGE_CLASSES
    templates.env.globals["verdict_labels"] = VERDICT_LABELS
    templates.env.globals["arena_role_labels"] = ARENA_ROLE_DISPLAY
    setup_flash(templates)
    app.state.arena_templates = templates

    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")

    # Stub routes required by _base.html and dashboard_login_history.html
    for path, name in [
        ("/", "arena_dashboard"),
        ("/status", "arena_status"),
        ("/auth/login", "arena_login"),
        ("/auth/signup", "arena_signup"),
        ("/user/profile", "arena_user_profile"),
        ("/problems", "arena_problem_list"),
        ("/classes", "arena_classes_index"),
        ("/classes/registered", "arena_classes_registered"),
        ("/classes/open", "arena_classes_open"),
        ("/classes/manage", "arena_classes_manage"),
        ("/ranking", "arena_ranking_index"),
        ("/ranking/users", "arena_ranking_users"),
        ("/ranking/affiliations", "arena_ranking_affiliations"),
        ("/help/rating", "arena_help_rating"),
        ("/help/languages", "arena_help_languages"),
        ("/admin/problems", "arena_admin_problem_list"),
        ("/admin/users", "arena_admin_user_list"),
        ("/admin/categories", "arena_admin_category_list"),
        ("/admin/affiliations", "arena_admin_affiliation_list"),
        ("/admin/dashboard", "arena_admin_dashboard"),
        ("/admin/dashboard/service-status", "arena_admin_dashboard_service_status"),
        ("/admin/dashboard/ai-credits", "arena_admin_dashboard_ai_credits"),
    ]:
        app.add_api_route(path, lambda: Response("stub"), name=name)  # type: ignore[arg-type]

    @app.post("/auth/logout", name="arena_logout")
    async def _logout() -> Response:
        return Response("logout")

    @app.get("/admin/users/{user_id}", name="arena_admin_user_profile")
    async def _admin_user_profile(user_id: str) -> Response:
        return Response(f"user {user_id}")

    @app.get("/submissions/{submission_id}", name="arena_submission_detail")
    async def _submission_detail(submission_id: str) -> Response:
        return Response(f"submission {submission_id}")

    @app.get("/user/avatar/{user_id}", name="arena_user_avatar_by_id")
    async def _avatar(user_id: str) -> Response:
        return Response("avatar", media_type="image/svg+xml")

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications() -> Response:
        return Response("[]", media_type="application/json")

    app.include_router(router)

    async def _get_db_override() -> Any:
        yield session

    app.dependency_overrides[get_db] = _get_db_override

    if authorized:

        async def _admin() -> SimpleNamespace:
            return SimpleNamespace(
                id="admin-1",
                email="admin@test.example",
                role=ArenaRole.ARENA_ADMIN,
                nome="Admin",
                dta_foto=None,
                timezone="UTC",
            )

        app.dependency_overrides[require_arena_admin] = _admin
    else:

        async def _reject() -> None:
            raise HTTPException(status_code=403)

        app.dependency_overrides[require_arena_admin] = _reject

    return app


@pytest.mark.asyncio
async def test_login_history_route_returns_200_for_admin(session: AsyncSession) -> None:
    """GET /admin/dashboard/login-history returns 200 for an authorized admin."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/login-history")
    assert response.status_code == 200
    assert "Login History" in response.text


@pytest.mark.asyncio
async def test_login_history_route_returns_403_for_non_admin(session: AsyncSession) -> None:
    """GET /admin/dashboard/login-history returns 403 for an unauthorized user."""
    app = _build_app(session, authorized=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/login-history")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_login_history_route_invalid_sort_dir_falls_back_to_desc(session: AsyncSession) -> None:
    """An unrecognised sort_dir is silently coerced to 'desc'; the page still loads."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/login-history", params={"sort_dir": "sideways"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_login_history_route_accepts_all_valid_per_page_sizes(session: AsyncSession) -> None:
    """All allowed per-page values (10/25/50/100/500) return 200."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for size in (10, 25, 50, 100, 500):
            response = await client.get("/admin/dashboard/login-history", params={"per_page": str(size)})
            assert response.status_code == 200, f"Expected 200 for per_page={size}"
