#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared app builder and fixtures for Arena admin problem route tests.

The admin problem routers need a fully wired Arena app (auth middleware, flash,
templates, static mounts, and every endpoint the shared layout resolves through
``url_for``). Building it once here keeps each route-test module focused on the
behaviour it covers instead of on 180 lines of scaffolding.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from fastapi import FastAPI
from fastapi.responses import Response
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_api import router as arena_admin_problem_api_router
from arena.routes.admin_problem_io import router as arena_admin_problem_io_router
from arena.routes.admin_problem_judgment import router as arena_admin_problem_judgment_router
from arena.routes.admin_problem_new import router as arena_admin_problem_new_router
from arena.routes.admin_problem_save import router as arena_admin_problem_save_router
from arena.routes.admin_problem_tc import router as arena_admin_problem_tc_router
from arena.routes.admin_problem_validator import router as arena_admin_problem_validator_router
from arena.routes.admin_problems import router as arena_admin_problems_router
from arena.routes.legal import router as arena_legal_router
from arena.routes.ranking import router as arena_ranking_router
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole
from tests.arena.conftest import install_arena_templates, mount_arena_base_routes
from web.models.language import Language

TEST_JWT_SECRET = "test-secret-key-for-admin-problem-tests-32b!"


def build_admin_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena FastAPI app for problem admin route tests."""
    app = FastAPI()
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

    @app.get("/help", name="arena_help_index")
    async def _help_index() -> Response:
        return Response("help")

    @app.get("/help/rating", name="arena_help_rating")
    async def _help_rating() -> Response:
        return Response("help")

    @app.get("/help/languages", name="arena_help_languages")
    async def _help_languages() -> Response:
        return Response("help")

    @app.get("/admin/users", name="arena_admin_user_list")
    async def _admin_users() -> Response:
        return Response("users")

    @app.get("/admin/users/{user_id}", name="arena_admin_user_profile")
    async def _admin_user_profile(user_id: str) -> Response:
        return Response(f"user {user_id}")

    @app.get("/admin/dashboard", name="arena_admin_dashboard")
    async def _admin_dashboard_stub() -> Response:
        return Response("dashboard")

    @app.get("/admin/categories", name="arena_admin_category_list")
    async def _admin_categories() -> Response:
        return Response("categories")

    @app.get("/admin/affiliations", name="arena_admin_affiliation_list")
    async def _admin_affiliations() -> Response:
        return Response("affiliations")

    @app.get("/problems", name="arena_problem_list")
    async def _problem_list() -> Response:
        return Response("problems")

    @app.get("/problems/{arena_number}", name="arena_problem_detail")
    async def _problem_detail(arena_number: int) -> Response:
        return Response(f"problem {arena_number}")

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

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications() -> Response:
        return Response("[]", media_type="application/json")

    app.include_router(arena_admin_problem_new_router)
    app.include_router(arena_admin_problems_router)
    app.include_router(arena_admin_problem_save_router)
    app.include_router(arena_admin_problem_judgment_router)
    app.include_router(arena_admin_problem_io_router)
    app.include_router(arena_admin_problem_tc_router)
    app.include_router(arena_admin_problem_api_router)
    app.include_router(arena_admin_problem_validator_router)
    app.include_router(arena_ranking_router)
    app.include_router(arena_legal_router)
    return app


async def create_user(
    session: AsyncSession,
    *,
    name: str = "Test User",
    email: str = "user@test.example",
    role: ArenaRole = ArenaRole.ARENA_USER,
    can_edit: bool = False,
) -> ArenaUser:
    user = ArenaUser(
        nome=name,
        email_normalizado=email,
        password_hash="hash",
        role=role,
        can_edit=can_edit,
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


async def create_language(session: AsyncSession) -> Language:
    language = Language(
        id=f"admin-route-test-{uuid.uuid4().hex[:8]}",
        name="Admin Route Test Language",
        icon="test",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="main.txt",
        artifact_path="/sandbox/main.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


def login_token(app: FastAPI, user: ArenaUser) -> str:
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )
