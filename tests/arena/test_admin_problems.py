#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for Arena admin problem management."""

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
from jinja2 import ChoiceLoader, FileSystemLoader
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.config import settings as arena_settings
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_problems import ArenaCategory
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_api import router as arena_admin_problem_api_router
from arena.routes.admin_problem_io import router as arena_admin_problem_io_router
from arena.routes.admin_problem_tc import router as arena_admin_problem_tc_router
from arena.routes.admin_problems import router as arena_admin_problems_router
from arena.routes.ranking import router as arena_ranking_router
from arena.services import admin_problem_service, admin_problem_tc_service
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from arena.services.token_service import ArenaTokenAction
from shared.enumerations import ArenaRole
from shared.tc_zip import MAX_INLINE_TESTCASE_BYTES

TEST_JWT_SECRET = "test-secret-key-for-admin-problem-tests-32b!"


def _build_admin_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena FastAPI app for problem admin route tests."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    arena_dir = Path(__file__).resolve().parents[2] / "arena"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=arena_dir / "template")
    templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(str(arena_dir / "template")),
            FileSystemLoader(str(shared_dir / "template")),
        ]
    )
    templates.env.globals["app_version"] = "test"
    templates.env.globals["next_rating_update_text"] = lambda request: None
    templates.env.globals["arena_role_labels"] = ARENA_ROLE_DISPLAY
    templates.env.globals["MAX_INLINE_TESTCASE_BYTES"] = MAX_INLINE_TESTCASE_BYTES
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

    app.include_router(arena_admin_problems_router)
    app.include_router(arena_admin_problem_io_router)
    app.include_router(arena_admin_problem_tc_router)
    app.include_router(arena_admin_problem_api_router)
    app.include_router(arena_ranking_router)
    return app


async def _create_user(
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


def _login_token(app: FastAPI, user: ArenaUser) -> str:
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


# ── Authorization tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_problem_list_requires_login(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/admin/problems")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_problem_list_denies_arena_user(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    user = await _create_user(session, email="u1@test.example", role=ArenaRole.ARENA_USER)
    token = _login_token(app, user)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_problem_list_denies_judge_without_can_edit(session: AsyncSession) -> None:
    """A judge without the can_edit grant may not manage the problem base."""
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j1@test.example", role=ArenaRole.ARENA_JUDGE)
    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_problem_list_allows_judge_with_can_edit(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j1edit@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_problem_list_allows_user_with_can_edit(session: AsyncSession) -> None:
    """The can_edit grant authorizes problem management regardless of role."""
    app = _build_admin_app(session)
    user = await _create_user(session, email="uedit@test.example", role=ArenaRole.ARENA_USER, can_edit=True)
    token = _login_token(app, user)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_problem_list_allows_admin(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_user(session, email="a1@test.example", role=ArenaRole.ARENA_ADMIN)
    token = _login_token(app, admin)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_problem_list_filters_categories_by_slug(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="jcat@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    category = ArenaCategory(name="Graphs", slug="graphs", color="#ff0000")
    session.add(category)
    await session.flush()
    await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Graph Paths",
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
        category_ids=[category.id],
    )
    await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Plain Math",
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
    )
    await session.commit()

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems?category_slugs=graphs")

    assert response.status_code == 200
    assert "Graph Paths" in response.text
    assert "Plain Math" not in response.text
    assert 'name="category_slugs"' in response.text
    assert 'value="graphs"' in response.text


# ── Create problem ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_problem_create_renders_form(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j2@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get("/admin/problems/new")
    assert response.status_code == 200
    assert "New Problem" in response.text
    assert 'id="author_is_owner"' in response.text
    assert 'id="author-field"' in response.text
    author_field_attributes = response.text.split('id="author-field"', 1)[1].split(">", 1)[0]
    assert "hidden" in author_field_attributes


@pytest.mark.asyncio
async def test_problem_create_success_redirects_to_list_with_highlight(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j3@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(
            "/admin/problems/new",
            data={
                "title": "My First Problem",
                "author_is_owner": "true",
                "source": "",
                "time_limit_ms": "1000",
                "memory_limit_kb": "262144",
                "pids_limit": "64",
                "output_limit_in_bytes": "65536",
                "problem_statement": "Hello",
            },
        )
    assert response.status_code == 303
    assert response.headers["location"].startswith("http://testserver/admin/problems#")


@pytest.mark.asyncio
async def test_problem_create_blank_title_returns_400(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j4@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.post(
            "/admin/problems/new",
            data={
                "title": "   ",
                "author_is_owner": "true",
                "time_limit_ms": "1000",
                "memory_limit_kb": "262144",
                "pids_limit": "64",
                "output_limit_in_bytes": "65536",
                "problem_statement": "x",
            },
        )
    assert response.status_code == 400


# ── Edit problem ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_problem_edit_shows_owner_link_to_admin(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    admin = await _create_user(
        session,
        name="Arena Admin",
        email="admin-problem-author@test.example",
        role=ArenaRole.ARENA_ADMIN,
    )
    author = await _create_user(
        session,
        name="Problem Author",
        email="problem-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=author.id,
        title="Authored Problem",
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
    )
    await session.commit()

    token = _login_token(app, admin)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.get(f"/admin/problems/{problem.id}/edit")

    assert response.status_code == 200
    assert "Problem Author" in response.text
    assert "Difficulty" in response.text
    assert f"/admin/users/{author.id}" in response.text


@pytest.mark.asyncio
async def test_problem_edit_hides_owner_link_from_judge(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(
        session,
        name="Judge Author",
        email="judge-problem-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Judge Problem",
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
    )
    await session.commit()

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.get(f"/admin/problems/{problem.id}/edit")

    assert response.status_code == 200
    assert f"/admin/users/{judge.id}" not in response.text


# ── Judge isolation ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_judge_cannot_edit_other_judges_problem(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge_a = await _create_user(session, email="ja@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    judge_b = await _create_user(session, email="jb@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    # Create a problem as judge_a
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge_a.id,
        title="Judge A Problem",
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
    )
    await session.commit()

    # Try to edit as judge_b
    token_b = _login_token(app, judge_b)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token_b}
    ) as client:
        response = await client.get(
            f"/admin/problems/{problem.id}/edit",
        )
    assert response.status_code == 404


# ── Update problem ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_problem_update_redirects_to_list(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j7@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Original Problem",
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
    )
    await session.commit()

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(
            f"/admin/problems/{problem.id}/edit",
            data={
                "title": "Updated Problem",
                "author": "External Writer",
                "license": "CC0 1.0",
                "source": "",
                "time_limit_ms": "1000",
                "memory_limit_kb": "262144",
                "pids_limit": "64",
                "output_limit_in_bytes": "65536",
                "problem_statement": "updated stmt",
                "return_page": "3",
                "return_per_page": "50",
                "return_search": "graphs",
                "return_sort_by": "rating_desc",
                "return_owner_id": judge.id,
                "return_category_slugs": ["graphs", "dynamic-programming"],
            },
        )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == "http://testserver/admin/problems?page=3&per_page=50&search=graphs&sort_by=rating_desc"
        f"&owner_id={judge.id}&category_slugs=graphs&category_slugs=dynamic-programming#{problem.id}"
    )
    await session.refresh(problem)
    assert problem.owner_id == judge.id
    assert problem.author == "External Writer"
    assert problem.author_is_owner is False
    assert problem.license == "CC0 1.0"


# ── Toggle enabled ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_toggle_enabled_redirects_to_list(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j5@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Toggle Problem",
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
    )
    await session.commit()

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(
            f"/admin/problems/{problem.id}/toggle-enabled?search=toggle&per_page=50&page=2",
        )
    assert response.status_code == 303
    assert response.headers["location"] == (
        f"http://testserver/admin/problems?page=2&per_page=50&search=toggle#{problem.id}"
    )

    # Verify flag changed in DB
    await session.refresh(problem)
    assert problem.enabled is True


# ── Toggle test-case sample/secret ────────────────────────────────────────────


async def _make_problem_with_tc(session: AsyncSession, owner_id: str, *, is_sample: bool = False) -> tuple[str, str]:
    """Create a problem with one test case; return (problem_id, tc_id)."""
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=owner_id,
        title="TC Toggle Problem",
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
    )
    tc, write_files = await admin_problem_tc_service.create_testcase(
        session,
        problem,
        input_content="1",
        output_content="1",
        is_sample=is_sample,
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    await session.commit()
    write_files()
    return problem.id, tc.id


@pytest.mark.asyncio
async def test_edit_page_renders_toggle_button_and_guard_scripts(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="tctoggle-render@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem_id, tc_id = await _make_problem_with_tc(session, judge.id)

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token}
    ) as client:
        response = await client.get(f"/admin/problems/{problem_id}/edit")

    assert response.status_code == 200
    body = response.text
    # Row anchor for the highlight pattern
    assert f'id="tc-{tc_id}"' in body
    # Toggle control posts to the new route
    assert f"/testcases/{tc_id}/toggle-sample" in body
    assert "swap_horiz" in body
    # UI warning + row highlight assets are wired on the edit page
    assert "problem-edit-unsaved-guard.js" in body
    assert "highlight-row.js" in body
    # Actions use the shared icon btn-group pattern, not loose text buttons
    assert "noca-icon-btn-group" in body
    assert "noca-icon-btn-wrap" in body
    assert ">Edit<" not in body
    assert ">Remove<" not in body


@pytest.mark.asyncio
async def test_toggle_sample_flips_and_redirects_to_row_anchor(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="tctoggle-post@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem_id, tc_id = await _make_problem_with_tc(session, judge.id, is_sample=False)

    token = _login_token(app, judge)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token},
    ) as client:
        response = await client.post(f"/admin/problems/{problem_id}/testcases/{tc_id}/toggle-sample")

    assert response.status_code == 303
    assert response.headers["location"] == (f"http://testserver/admin/problems/{problem_id}/edit#tc-{tc_id}")

    tc = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem_id)
    assert tc is not None
    assert tc.is_sample is True


@pytest.mark.asyncio
async def test_judge_cannot_toggle_other_judges_testcase(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge_a = await _create_user(session, email="tctoggle-a@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    judge_b = await _create_user(session, email="tctoggle-b@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    problem_id, tc_id = await _make_problem_with_tc(session, judge_a.id, is_sample=False)

    token_b = _login_token(app, judge_b)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        cookies={"arena_access_token": token_b},
    ) as client:
        response = await client.post(f"/admin/problems/{problem_id}/testcases/{tc_id}/toggle-sample")

    assert response.status_code == 404
    # Flag must remain unchanged
    tc = await admin_problem_tc_service.get_testcase(session, tc_id, problem_id=problem_id)
    assert tc is not None
    assert tc.is_sample is False


# ── Admin sees all problems ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_list_shows_all_problems(session: AsyncSession) -> None:
    app = _build_admin_app(session)
    judge = await _create_user(session, email="j6@test.example", role=ArenaRole.ARENA_JUDGE, can_edit=True)
    admin = await _create_user(session, email="adm2@test.example", role=ArenaRole.ARENA_ADMIN)

    problem = await admin_problem_service.create_problem(
        session,
        caller_id=judge.id,
        title="Visible to Admin",
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
    )
    await session.commit()

    token_admin = _login_token(app, admin)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", cookies={"arena_access_token": token_admin}
    ) as client:
        response = await client.get("/admin/problems")
    assert response.status_code == 200
    assert "Visible to Admin" in response.text
    assert "Difficulty" in response.text
    assert f'id="{problem.id}"' in response.text
    assert "highlight-row.js" in response.text
