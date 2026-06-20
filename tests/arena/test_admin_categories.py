#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for Arena admin category CRUD."""

import logging
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_problems import ArenaCategory
from arena.models.arena_users import ArenaUser
from arena.routes.admin_categories import router as arena_admin_categories_router
from arena.routes.ranking import router as arena_ranking_router
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole

TEST_JWT_SECRET = "test-secret-key-for-admin-category-tests-32b!"


def _build_admin_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena FastAPI app for category admin route tests."""
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

    @app.get("/", name="arena_dashboard")
    async def _dashboard() -> Response:
        return Response("dashboard")

    @app.get("/status", name="arena_status")
    async def _status() -> Response:
        return Response("status")

    @app.get("/auth/login", name="arena_login")
    async def _login() -> Response:
        return Response("login")

    @app.get("/auth/signup", name="arena_signup")
    async def _signup() -> Response:
        return Response("signup")

    @app.post("/auth/logout", name="arena_logout")
    async def _logout() -> Response:
        return Response("logout")

    @app.get("/user/profile", name="arena_user_profile")
    async def _profile() -> Response:
        return Response("profile")

    @app.get("/user/avatar/{user_id}", name="arena_user_avatar_by_id")
    async def _avatar(user_id: str) -> Response:
        return Response("avatar", media_type="image/svg+xml")

    @app.get("/help/rating", name="arena_help_rating")
    async def _help_rating() -> Response:
        return Response("help")

    @app.get("/help/languages", name="arena_help_languages")
    async def _help_languages() -> Response:
        return Response("help")

    @app.get("/problems", name="arena_problem_list")
    async def _problem_list() -> Response:
        return Response("problems")

    @app.get("/classes", name="arena_classes_index")
    async def _classes_index() -> Response:
        return Response("classes")

    @app.get("/classes/registered", name="arena_classes_registered")
    async def _classes_registered() -> Response:
        return Response("classes registered")

    @app.get("/classes/open", name="arena_classes_open")
    async def _classes_open() -> Response:
        return Response("classes open")

    @app.get("/classes/manage", name="arena_classes_manage")
    async def _classes_manage() -> Response:
        return Response("classes manage")

    @app.get("/admin/users", name="arena_admin_user_list")
    async def _admin_users() -> Response:
        return Response("users")

    @app.get("/admin/dashboard", name="arena_admin_dashboard")
    async def _admin_dashboard_stub() -> Response:
        return Response("dashboard")

    @app.get("/admin/problems", name="arena_admin_problem_list")
    async def _admin_problems() -> Response:
        return Response("problems")

    @app.get("/admin/affiliations", name="arena_admin_affiliation_list")
    async def _admin_affiliations() -> Response:
        return Response("affiliations")

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications() -> Response:
        return Response("[]", media_type="application/json")

    app.include_router(arena_admin_categories_router)
    app.include_router(arena_ranking_router)
    return app


async def _create_arena_user(
    session: AsyncSession,
    *,
    name: str = "Test User",
    email: str = "user@test.example",
    role: ArenaRole = ArenaRole.ARENA_USER,
) -> ArenaUser:
    """Create and flush a minimal Arena user."""
    user = ArenaUser(
        nome=name,
        email_normalizado=email,
        password_hash="hash",
        role=role,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        session_version=0,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _login_token(app: FastAPI, user: ArenaUser) -> str:
    """Issue a valid Arena login token for the given user."""
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


@pytest.mark.asyncio
async def test_admin_category_list_renders_for_admin(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    session.add(ArenaCategory(name="Graphs", slug="graphs", color="#0d6efd"))
    await session.commit()
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/categories")

    assert response.status_code == 200
    assert "Graphs" in response.text
    assert "graphs" in response.text
    assert 'data-category-color="#0d6efd"' in response.text
    assert "arena_admin_category_edit" not in response.text
    assert "data-category-delete-button" in response.text


@pytest.mark.asyncio
async def test_admin_category_list_requires_admin(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    user = await _create_arena_user(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        forbidden = await client.get("/admin/categories")
        client.cookies.delete("arena_access_token")
        unauthenticated = await client.get("/admin/categories")

    assert forbidden.status_code == 403
    assert unauthenticated.status_code == 401


@pytest.mark.asyncio
async def test_admin_category_create_edit_delete_flow(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        create = await client.post(
            "/admin/categories/new",
            data={"name": "Dynamic Programming", "slug": "dynamic-programming", "color": "#ABCDEF"},
            follow_redirects=False,
        )
    assert create.status_code == 303
    category = (await session.execute(select(ArenaCategory))).scalar_one()
    assert category.color == "#abcdef"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        edit_page = await client.get(
            f"/admin/categories/{category.id}/edit",
        )
        update = await client.post(
            f"/admin/categories/{category.id}/edit",
            data={"name": "DP", "slug": "dp", "color": "#123456"},
            follow_redirects=False,
        )
    assert edit_page.status_code == 200
    assert "data-category-delete-button" in edit_page.text
    assert update.status_code == 303
    await session.refresh(category)
    assert category.name == "DP"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        delete = await client.post(
            f"/admin/categories/{category.id}/delete",
            follow_redirects=False,
        )
    assert delete.status_code == 303
    category_id = category.id
    session.expire(category)
    assert await session.get(ArenaCategory, category_id) is None


@pytest.mark.asyncio
async def test_admin_category_create_validation_rerenders_form(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            "/admin/categories/new",
            data={"name": "", "slug": "", "color": "#123456"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert (await session.execute(select(ArenaCategory))).scalars().first() is None
