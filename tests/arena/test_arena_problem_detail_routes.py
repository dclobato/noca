#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for Arena public problem detail pages."""

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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.config import settings as arena_settings
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.routes.problems import router as arena_problems_router
from arena.services import admin_problem_service, admin_problem_tc_service
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole

TEST_JWT_SECRET = "test-secret-key-for-problem-detail-tests"


def _build_problem_detail_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena FastAPI app for public problem detail tests."""
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

    @app.get("/admin/problems/{problem_id}/edit", name="arena_admin_problem_edit")
    async def _admin_problem_edit(problem_id: str) -> Response:
        return Response(f"edit {problem_id}")

    @app.get("/admin/problems", name="arena_admin_problem_list")
    async def _admin_problems() -> Response:
        return Response("problems")

    @app.get("/admin/users", name="arena_admin_user_list")
    async def _admin_users() -> Response:
        return Response("users")

    @app.get("/admin/dashboard", name="arena_admin_dashboard")
    async def _admin_dashboard_stub() -> Response:
        return Response("dashboard")

    @app.get("/admin/categories", name="arena_admin_category_list")
    async def _admin_categories() -> Response:
        return Response("categories")

    @app.get("/admin/affiliations", name="arena_admin_affiliation_list")
    async def _admin_affiliations() -> Response:
        return Response("affiliations")

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

    @app.get("/ranking", name="arena_ranking_index")
    async def _ranking() -> Response:
        return Response("ranking")

    @app.get("/help/rating", name="arena_help_rating")
    async def _help_rating() -> Response:
        return Response("help")

    @app.get("/help/languages", name="arena_help_languages")
    async def _help_languages() -> Response:
        return Response("help")

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications() -> Response:
        return Response("[]", media_type="application/json")

    app.include_router(arena_problems_router)
    return app


async def _create_user(
    session: AsyncSession,
    *,
    name: str,
    email: str,
    role: ArenaRole,
    can_edit: bool = False,
) -> ArenaUser:
    """Create an Arena user for route tests."""
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
        can_edit=can_edit,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _login_token(app: FastAPI, user: ArenaUser) -> str:
    """Build a login token for the given Arena user."""
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


async def _create_enabled_problem(
    session: AsyncSession,
    author: ArenaUser,
    *,
    license: str | None = None,
) -> ArenaProblem:
    """Create an enabled problem for public detail route tests."""
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=author.id,
        title="Visible Problem",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="stmt",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
        license=license,
    )
    problem.enabled = True
    await session.commit()
    await session.refresh(problem)
    return problem


@pytest.mark.asyncio
async def test_problem_detail_renders_resizable_workspace(session: AsyncSession) -> None:
    """Problem detail includes the accessible desktop column resizer."""
    app = _build_problem_detail_app(session)
    author = await _create_user(
        session,
        name="Workspace Author",
        email="workspace-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    problem = await _create_enabled_problem(session, author)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(f"/problems/{problem.arena_number}")

    assert response.status_code == 200
    assert "Difficulty" in response.text
    assert "data-problem-workspace" in response.text
    assert "data-problem-workspace-resizer" in response.text
    assert 'role="separator"' in response.text
    assert 'aria-controls="problem-statement-panel solution-panel"' in response.text
    assert "problem-column-resizer.js?v=test" in response.text


@pytest.mark.asyncio
async def test_problem_detail_renders_license_before_sample_download(session: AsyncSession) -> None:
    """A problem license is escaped and displayed before the sample ZIP link."""
    app = _build_problem_detail_app(session)
    author = await _create_user(
        session,
        name="Licensed Author",
        email="licensed-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    problem = await _create_enabled_problem(session, author, license="CC BY & ShareAlike")
    _tc, write_files = await admin_problem_tc_service.create_testcase(
        session,
        problem,
        input_content="1\n",
        output_content="1\n",
        is_sample=True,
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    await session.commit()
    write_files()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(f"/problems/{problem.arena_number}")

    assert response.status_code == 200
    license_position = response.text.index("License:</span> CC BY &amp; ShareAlike")
    download_position = response.text.index("Download sample test cases")
    assert license_position < download_position


@pytest.mark.asyncio
async def test_problem_detail_omits_empty_license(session: AsyncSession) -> None:
    """Problems without a license do not render a license label."""
    app = _build_problem_detail_app(session)
    author = await _create_user(
        session,
        name="Unlicensed Author",
        email="unlicensed-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    problem = await _create_enabled_problem(session, author)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(f"/problems/{problem.arena_number}")

    assert response.status_code == 200
    assert "License:</span>" not in response.text


@pytest.mark.asyncio
async def test_problem_detail_edit_button_visibility_by_role(session: AsyncSession) -> None:
    app = _build_problem_detail_app(session)
    author_with_edit = await _create_user(
        session,
        name="Author With Edit",
        email="author-with-edit@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    author_no_edit = await _create_user(
        session,
        name="Author No Edit",
        email="author-no-edit@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=False,
    )
    admin = await _create_user(
        session,
        name="Arena Admin",
        email="detail-admin@test.example",
        role=ArenaRole.ARENA_ADMIN,
    )
    other_judge = await _create_user(
        session,
        name="Other Judge",
        email="other-judge@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    regular_user = await _create_user(
        session,
        name="Regular User",
        email="regular-user@test.example",
        role=ArenaRole.ARENA_USER,
    )
    problem = await _create_enabled_problem(session, author_with_edit)
    edit_href = f"/admin/problems/{problem.id}/edit?next=/problems/{problem.arena_number}"

    async def can_see_edit_link(user: ArenaUser) -> bool:
        token = _login_token(app, user)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            cookies={"arena_access_token": token},
        ) as client:
            response = await client.get(f"/problems/{problem.arena_number}")
        assert response.status_code == 200
        return edit_href in response.text

    # Admin always sees the edit link
    assert await can_see_edit_link(admin) is True
    # Author with can_edit sees the edit link
    assert await can_see_edit_link(author_with_edit) is True
    # Author whose can_edit was revoked does not see the edit link
    assert await can_see_edit_link(author_no_edit) is False
    # Judge with can_edit but not the author does not see the edit link
    assert await can_see_edit_link(other_judge) is False
    assert await can_see_edit_link(regular_user) is False


@pytest.mark.asyncio
async def test_sidebar_manage_problems_visibility(session: AsyncSession) -> None:
    """Sidebar 'Manage problems' appears only for ARENA_ADMIN or users with can_edit."""
    app = _build_problem_detail_app(session)
    admin = await _create_user(
        session,
        name="Admin",
        email="sidebar-admin@test.example",
        role=ArenaRole.ARENA_ADMIN,
    )
    judge_with_edit = await _create_user(
        session,
        name="Judge Can Edit",
        email="sidebar-judge-edit@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    judge_no_edit = await _create_user(
        session,
        name="Judge No Edit",
        email="sidebar-judge-noedit@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=False,
    )
    regular_user = await _create_user(
        session,
        name="Regular",
        email="sidebar-regular@test.example",
        role=ArenaRole.ARENA_USER,
    )
    problem = await _create_enabled_problem(session, admin)

    async def can_see_manage_problems(user: ArenaUser) -> bool:
        token = _login_token(app, user)
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            cookies={"arena_access_token": token},
        ) as client:
            response = await client.get(f"/problems/{problem.arena_number}")
        assert response.status_code == 200
        return "Manage problems" in response.text

    assert await can_see_manage_problems(admin) is True
    assert await can_see_manage_problems(judge_with_edit) is True
    assert await can_see_manage_problems(judge_no_edit) is False
    assert await can_see_manage_problems(regular_user) is False
