#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for Arena admin collection CRUD."""

import logging
from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_problems import ArenaCollection
from arena.models.arena_users import ArenaUser
from arena.routes.admin_collections import router as arena_admin_collections_router
from arena.routes.legal import router as arena_legal_router
from arena.routes.ranking import router as arena_ranking_router
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole
from tests.arena.conftest import install_arena_templates, mount_arena_base_routes

TEST_JWT_SECRET = "test-secret-key-for-admin-collection-tests-32b!"


def _build_admin_app(session: AsyncSession, *, dependencies: list[Any] | None = None) -> FastAPI:
    """Build a minimal Arena FastAPI app for collection admin route tests.

    Args:
        session: Test database session whose engine backs the app.
        dependencies: Optional app-level dependencies, for tests of the global
            per-request hooks ``arena/main.py`` registers.
    """
    app = FastAPI(dependencies=dependencies)
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    install_arena_templates(app)
    mount_arena_base_routes(app)
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

    @app.get("/", name="arena_dashboard")
    async def _dashboard() -> Response:
        return Response("dashboard")

    @app.get("/live", name="arena_live")
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

    @app.get("/admin/dashboard/service-status", name="arena_admin_dashboard_service_status")
    async def _dash_service_status() -> Response:
        return Response("stub")

    @app.get("/admin/dashboard/security-events", name="arena_admin_dashboard_security_events")
    async def _dash_security_events() -> Response:
        return Response("stub")

    @app.get("/admin/dashboard/login-history", name="arena_admin_dashboard_login_history")
    async def _dash_login_history() -> Response:
        return Response("stub")

    @app.get("/admin/dashboard/submissions", name="arena_admin_dashboard_submissions")
    async def _dash_submissions() -> Response:
        return Response("stub")

    @app.get("/admin/dashboard/ai-usage", name="arena_admin_dashboard_ai_usage")
    async def _dash_ai_usage() -> Response:
        return Response("stub")

    @app.get("/admin/dashboard/terms", name="arena_admin_dashboard_terms")
    async def _dash_terms() -> Response:
        return Response("stub")

    @app.get("/help", name="arena_help_index")
    async def _help_index() -> Response:
        return Response("help")

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

    @app.get("/admin/categories", name="arena_admin_category_list")
    async def _admin_categories() -> Response:
        return Response("categories")

    @app.get("/admin/affiliations", name="arena_admin_affiliation_list")
    async def _admin_affiliations() -> Response:
        return Response("affiliations")

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications() -> Response:
        return Response("[]", media_type="application/json")

    app.include_router(arena_admin_collections_router)
    app.include_router(arena_ranking_router)
    app.include_router(arena_legal_router)
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
async def test_admin_collection_list_renders_for_admin(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    session.add(ArenaCollection(name="InterIF", slug="interif"))
    await session.commit()
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/collections")

    assert response.status_code == 200
    assert "InterIF" in response.text
    assert "interif" in response.text
    # The name links into the admin problem list scoped to this collection. The
    # query key must be the one that route actually reads (`collection`); a
    # plausible-looking `collection_slug` is silently ignored and opens the
    # unfiltered list.
    assert "?collection=interif" in response.text
    assert "?collection_slug=" not in response.text
    assert "arena_admin_collection_edit" not in response.text
    assert "data-taxonomy-delete-button" in response.text


@pytest.mark.asyncio
async def test_admin_collection_list_requires_admin(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    user = await _create_arena_user(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        forbidden = await client.get("/admin/collections")
        client.cookies.delete("arena_access_token")
        unauthenticated = await client.get("/admin/collections")

    assert forbidden.status_code == 403
    assert unauthenticated.status_code == 401


@pytest.mark.asyncio
async def test_admin_collection_create_edit_delete_flow(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        create = await client.post(
            "/admin/collections/new",
            data={"name": "Maratona SBC", "slug": "maratona-sbc"},
            follow_redirects=False,
        )
    assert create.status_code == 303
    collection = (await session.execute(select(ArenaCollection))).scalar_one()
    assert collection.slug == "maratona-sbc"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        edit_page = await client.get(
            f"/admin/collections/{collection.id}/edit",
        )
        update = await client.post(
            f"/admin/collections/{collection.id}/edit",
            data={"name": "ICPC", "slug": "icpc"},
            follow_redirects=False,
        )
    assert edit_page.status_code == 200
    assert "data-taxonomy-delete-button" in edit_page.text
    assert update.status_code == 303
    await session.refresh(collection)
    assert collection.name == "ICPC"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        delete = await client.post(
            f"/admin/collections/{collection.id}/delete",
            follow_redirects=False,
        )
    assert delete.status_code == 303
    collection_id = collection.id
    session.expire(collection)
    assert await session.get(ArenaCollection, collection_id) is None


@pytest.mark.asyncio
async def test_admin_collection_create_validation_rerenders_form(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_arena_user(session, role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            "/admin/collections/new",
            data={"name": "", "slug": ""},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert (await session.execute(select(ArenaCollection))).scalars().first() is None
