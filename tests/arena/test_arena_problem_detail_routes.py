#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for Arena public problem detail pages."""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
from jinja2 import ChoiceLoader, FileSystemLoader
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_problem_sets  # noqa: F401
import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.config import settings as arena_settings
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_classes import ArenaClass
from arena.models.arena_problem_sets import ArenaProblemSet
from arena.models.arena_problems import ArenaProblem, ArenaProblemCustomValidator
from arena.models.arena_users import ArenaUser
from arena.routes.legal import router as arena_legal_router
from arena.routes.problem_editorial import router as arena_problem_editorial_router
from arena.routes.problem_problem_sets import router as arena_problem_problem_sets_router
from arena.routes.problems import router as arena_problems_router
from arena.services import admin_problem_interaction_service, admin_problem_service, admin_problem_tc_service
from arena.services.token_service import ArenaTokenAction
from arena.services.user_timezone_service import format_user_datetime
from shared.db_schema.arena import arena_problem_set_problems, arena_problem_solvers
from shared.enumerations import (
    ArenaEditorialReleasePolicy,
    ArenaRole,
    CustomValidatorActiveState,
    ProblemValidatorType,
    StatementLanguage,
)
from shared.services.sample_interactions import parse_interaction_text
from tests.arena.conftest import install_arena_templates, mount_arena_base_routes
from web.models.language import Language

TEST_JWT_SECRET = "test-secret-key-for-problem-detail-tests"

# Rendered by problem_detail.html when an editorial exists but is still gated
# behind an Accepted verdict for the viewing user.
_EDITORIAL_PENDING_HINT = "Editorial available after AC"


def _build_problem_detail_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena FastAPI app for public problem detail tests."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    repo_root = Path(__file__).resolve().parents[2]
    arena_dir = repo_root / "arena"
    templates = install_arena_templates(app)
    # Mirror arena/main.py so shared/_partials (e.g. the problem image figure) resolve.
    templates.env.loader = ChoiceLoader(
        [
            FileSystemLoader(arena_dir / "template"),
            FileSystemLoader(repo_root / "shared" / "template"),
        ]
    )
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

    @app.get(
        "/classes/{class_id}/problem-sets/{set_id}/problems",
        name="arena_class_problem_set_manage",
    )
    async def _problem_set_manage(class_id: str, set_id: str) -> Response:
        return Response(f"manage {class_id} {set_id}")

    @app.get("/ranking", name="arena_ranking_index")
    async def _ranking() -> Response:
        return Response("ranking")

    @app.get("/help", name="arena_help_index")
    async def _help_index() -> Response:
        return Response("help")

    @app.get("/help/rating", name="arena_help_rating")
    async def _help_rating() -> Response:
        return Response("help")

    @app.get("/help/languages", name="arena_help_languages")
    async def _help_languages() -> Response:
        return Response("help")

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications() -> Response:
        return Response("[]", media_type="application/json")

    app.include_router(arena_problem_problem_sets_router)
    app.include_router(arena_problems_router)
    app.include_router(arena_problem_editorial_router)
    app.include_router(arena_legal_router)
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


async def _create_language(session: AsyncSession) -> Language:
    language = Language(
        id=f"problem-detail-test-{uuid.uuid4().hex[:8]}",
        name="Problem Detail Test Language",
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
    title: str = "Visible Problem",
    validator_type: ProblemValidatorType = ProblemValidatorType.STANDARD,
) -> ArenaProblem:
    """Create an enabled problem for public detail route tests."""
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=author.id,
        title=title,
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
        validator_type=validator_type,
    )
    problem.enabled = True
    await session.commit()
    await session.refresh(problem)
    return problem


async def _create_problem_set(
    session: AsyncSession,
    *,
    teacher: ArenaUser,
    name: str,
    deadline: datetime | None,
    starts_on: datetime | None = None,
    class_name: str = "Teacher class",
) -> tuple[ArenaClass, ArenaProblemSet]:
    """Create an ongoing teacher-owned class and one problem set."""
    today = date.today()
    arena_class = ArenaClass(
        name=class_name,
        teacher_id=teacher.id,
        starts_on=today - timedelta(days=7),
        finishes_on=today + timedelta(days=30),
    )
    session.add(arena_class)
    await session.flush()
    problem_set = ArenaProblemSet(
        class_id=arena_class.id,
        name=name,
        starts_on=starts_on,
        deadline=deadline,
    )
    session.add(problem_set)
    await session.commit()
    return arena_class, problem_set


async def _mark_solved(session: AsyncSession, *, problem: ArenaProblem, user: ArenaUser) -> None:
    """Record an AC for (problem, user) via ``arena_problem_solvers``."""
    await session.execute(
        arena_problem_solvers.insert().values(
            problem_id=problem.id,
            user_id=user.id,
            solved_at=datetime.now(UTC),
        )
    )
    await session.commit()


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
        cookies={"arena_access_token": _login_token(app, author)},
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
async def test_problem_detail_renders_brazilian_affiliation_state_flag(
    session: AsyncSession,
) -> None:
    """A Brazilian author's affiliation displays its country and state flags."""
    app = _build_problem_detail_app(session)
    affiliation = ArenaAffiliation(
        name="Universidade de Teste",
        country_code="BR",
        subdivision_code="BR-RS",
    )
    session.add(affiliation)
    await session.flush()
    author = await _create_user(
        session,
        name="Brazilian Author",
        email="brazilian-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    author.affiliation_id = affiliation.id
    problem = await _create_enabled_problem(session, author)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, author)},
    ) as client:
        response = await client.get(f"/problems/{problem.arena_number}")

    assert response.status_code == 200
    assert "/static/vendor/img/state-flags/BR.svg" in response.text
    assert "/static/vendor/img/state-flags/RS.svg" in response.text


@pytest.mark.asyncio
async def test_problem_detail_renders_teacher_assignment_card(session: AsyncSession) -> None:
    """A judge sees grouped assignments, safe target data, and the external controller."""
    app = _build_problem_detail_app(session)
    teacher = await _create_user(
        session,
        name="Assignment Teacher",
        email="assignment-teacher@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    problem = await _create_enabled_problem(session, teacher)
    now = datetime.now(UTC)
    arena_class, assigned_set = await _create_problem_set(
        session,
        teacher=teacher,
        name="Assigned set",
        deadline=now + timedelta(days=2),
        class_name="Algorithms",
    )
    target_name = "Target </script><script>alert(1)</script>"
    target_set = ArenaProblemSet(
        class_id=arena_class.id,
        name=target_name,
        starts_on=now + timedelta(days=5),
        deadline=None,
    )
    session.add(target_set)
    await session.execute(
        arena_problem_set_problems.insert().values(
            problem_set_id=assigned_set.id,
            problem_id=problem.id,
        )
    )
    await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, teacher)},
    ) as client:
        response = await client.get(
            f"/problems/{problem.arena_number}",
            params=[
                ("back_page", "3"),
                ("back_search", "graphs"),
                ("back_sort_by", "rating_desc"),
                ("back_category_slugs", "dp"),
                ("back_category_slugs", "graphs"),
            ],
        )

    assert response.status_code == 200
    body = response.text
    assert body.index("Problem set assignment") < body.index("data-problem-workspace")
    assert 'class="accordion-button collapsed' in body
    assert 'aria-expanded="false"' in body
    assert 'class="accordion-collapse collapse"' in body
    assert 'class="accordion-collapse collapse show"' not in body
    assert "Current assignments" in body
    assert "Algorithms" in body
    assert "Assigned set" in body
    assert (f'href="http://testserver/classes/{arena_class.id}/problem-sets/{assigned_set.id}/problems"') in body
    assert "Open deadline" not in body
    assert "problem-set-assignment-options" in body
    assert "\\u003c/script\\u003e" in body
    assert "</script><script>alert(1)</script>" not in body
    assert "problem-set-assignment.js?v=test" in body
    assert 'name="back_page" value="3"' in body
    assert body.count('name="back_category_slugs"') == 2


@pytest.mark.asyncio
async def test_problem_detail_hides_assignment_card_from_admin_and_user(
    session: AsyncSession,
) -> None:
    """Arena admins and regular users do not receive teacher assignment controls."""
    app = _build_problem_detail_app(session)
    teacher = await _create_user(
        session,
        name="Card Teacher",
        email="card-teacher@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    admin = await _create_user(
        session,
        name="Card Admin",
        email="card-admin@test.example",
        role=ArenaRole.ARENA_ADMIN,
    )
    user = await _create_user(
        session,
        name="Card User",
        email="card-user@test.example",
        role=ArenaRole.ARENA_USER,
    )
    problem = await _create_enabled_problem(session, teacher)
    await _create_problem_set(
        session,
        teacher=teacher,
        name="Teacher target",
        deadline=None,
    )

    for actor in (admin, user):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            cookies={"arena_access_token": _login_token(app, actor)},
        ) as client:
            response = await client.get(f"/problems/{problem.arena_number}")
        assert response.status_code == 200
        assert "Problem set assignment" not in response.text
        assert "problem-set-assignment.js" not in response.text


@pytest.mark.asyncio
async def test_problem_assignment_post_inserts_and_preserves_return_state(
    session: AsyncSession,
) -> None:
    """A valid POST inserts membership, flashes success, and preserves list state."""
    app = _build_problem_detail_app(session)
    teacher = await _create_user(
        session,
        name="Post Teacher",
        email="post-teacher@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    problem = await _create_enabled_problem(session, teacher)
    _arena_class, problem_set = await _create_problem_set(
        session,
        teacher=teacher,
        name="Future target",
        starts_on=datetime.now(UTC) + timedelta(days=5),
        deadline=None,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, teacher)},
    ) as client:
        response = await client.post(
            f"/problems/{problem.arena_number}/problem-sets",
            data={
                "problem_set_id": problem_set.id,
                "back_page": "4",
                "back_search": "trees",
                "back_sort_by": "title_desc",
                "back_category_slugs": ["graphs", "trees"],
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"].endswith(
            f"/problems/{problem.arena_number}"
            "?back_page=4&back_search=trees&back_sort_by=title_desc"
            "&back_category_slugs=graphs&back_category_slugs=trees"
        )
        detail = await client.get(response.headers["location"])

    membership = await session.scalar(
        select(arena_problem_set_problems.c.problem_id).where(
            arena_problem_set_problems.c.problem_set_id == problem_set.id,
            arena_problem_set_problems.c.problem_id == problem.id,
        )
    )
    assert membership == problem.id
    assert "Problem added to the problem set." in detail.text


@pytest.mark.asyncio
async def test_problem_assignment_post_maps_authorization_missing_and_stale(
    session: AsyncSession,
) -> None:
    """POST returns 403/404 and warning redirects without duplicate membership."""
    app = _build_problem_detail_app(session)
    teacher = await _create_user(
        session,
        name="Mutation Teacher",
        email="mutation-teacher@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    other = await _create_user(
        session,
        name="Mutation Other",
        email="mutation-other@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    problem = await _create_enabled_problem(session, teacher)
    _owned_class, populated_set = await _create_problem_set(
        session,
        teacher=teacher,
        name="Populated",
        deadline=None,
    )
    _foreign_class, foreign_set = await _create_problem_set(
        session,
        teacher=other,
        name="Foreign",
        deadline=None,
    )
    await session.execute(
        arena_problem_set_problems.insert().values(
            problem_set_id=populated_set.id,
            problem_id=problem.id,
        )
    )
    await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, teacher)},
    ) as client:
        foreign = await client.post(
            f"/problems/{problem.arena_number}/problem-sets",
            data={"problem_set_id": foreign_set.id},
        )
        missing = await client.post(
            "/problems/2000000000/problem-sets",
            data={"problem_set_id": populated_set.id},
        )
        stale = await client.post(
            f"/problems/{problem.arena_number}/problem-sets",
            data={"problem_set_id": populated_set.id},
            follow_redirects=True,
        )

    assert foreign.status_code == 403
    assert missing.status_code == 404
    assert stale.status_code == 200
    assert "already in the selected problem set" in stale.text
    memberships = await session.scalar(
        select(func.count())
        .select_from(arena_problem_set_problems)
        .where(
            arena_problem_set_problems.c.problem_set_id == populated_set.id,
            arena_problem_set_problems.c.problem_id == problem.id,
        )
    )
    assert memberships == 1


@pytest.mark.asyncio
async def test_problem_detail_renders_custom_validator_banner(session: AsyncSession) -> None:
    app = _build_problem_detail_app(session)
    author = await _create_user(
        session,
        name="Validator Author",
        email="validator-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    language = await _create_language(session)
    plain_problem = await _create_enabled_problem(session, author, title="Plain Detail")
    validator_problem = await _create_enabled_problem(
        session, author, title="Interactive Detail", validator_type=ProblemValidatorType.INTERACTIVE
    )
    session.add(
        ArenaProblemCustomValidator(
            problem_id=validator_problem.id,
            active_language_id=language.id,
            active_source="print('validator')\n",
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=datetime.now(UTC),
        )
    )
    await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, author)},
    ) as client:
        validator_response = await client.get(f"/problems/{validator_problem.arena_number}")
        plain_response = await client.get(f"/problems/{plain_problem.arena_number}")

    assert validator_response.status_code == 200
    assert "interactive</strong> problem" in validator_response.text
    assert "published_with_changes" in validator_response.text
    assert plain_response.status_code == 200
    assert "interactive</strong> problem" not in plain_response.text


@pytest.mark.asyncio
async def test_interactive_problem_renders_sample_interactions_not_test_cases(session: AsyncSession) -> None:
    """An interactive problem shows authored conversations instead of sample cases."""
    app = _build_problem_detail_app(session)
    author = await _create_user(
        session,
        name="Sample Author",
        email="sample-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    language = await _create_language(session)
    problem = await _create_enabled_problem(
        session, author, title="Guess The Number", validator_type=ProblemValidatorType.INTERACTIVE
    )
    session.add(
        ArenaProblemCustomValidator(
            problem_id=problem.id,
            active_language_id=language.id,
            active_source="print('validator')\n",
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=datetime.now(UTC),
        )
    )
    await session.flush()

    # The problem's own cases are secret and output-less; they never reach the page.
    _tc, write_files = await admin_problem_tc_service.create_testcase(
        session,
        problem,
        input_content="SECRETCASEINPUT\n",
        output_content="",
        is_sample=False,
        testcase_dir=arena_settings.PROBLEM_TESTCASE_DIR,
    )
    await admin_problem_interaction_service.create_interaction(
        session,
        problem,
        transcript=parse_interaction_text("> 3\n< 5\n> !8"),
        explanation="The hidden number is 8.",
    )
    await session.commit()
    write_files()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, author)},
    ) as client:
        response = await client.get(f"/problems/{problem.arena_number}")

    assert response.status_code == 200
    body = response.text
    assert "Sample Interactions" in body
    assert "Sample Test Cases" not in body
    assert "The hidden number is 8." in body
    # The conversation renders through the shared transcript table.
    assert "noca-transcript-validator" in body
    assert "noca-transcript-user" in body
    # The secret case's input must not leak onto the public statement. The marker is
    # deliberately distinctive: a bare number would collide with an arena number or a
    # UUID fragment elsewhere on the page.
    assert "SECRETCASEINPUT" not in body


@pytest.mark.asyncio
async def test_interactive_problem_without_interactions_says_so(session: AsyncSession) -> None:
    """An interactive problem with no authored conversations shows an empty state."""
    app = _build_problem_detail_app(session)
    author = await _create_user(
        session,
        name="Empty Author",
        email="empty-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    language = await _create_language(session)
    problem = await _create_enabled_problem(
        session, author, title="No Samples", validator_type=ProblemValidatorType.INTERACTIVE
    )
    session.add(
        ArenaProblemCustomValidator(
            problem_id=problem.id,
            active_language_id=language.id,
            active_source="print('validator')\n",
            active_state=CustomValidatorActiveState.VALID,
            active_validated_at=datetime.now(UTC),
        )
    )
    await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, author)},
    ) as client:
        response = await client.get(f"/problems/{problem.arena_number}")

    assert response.status_code == 200
    assert "No sample interactions for this problem." in response.text


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
        cookies={"arena_access_token": _login_token(app, author)},
    ) as client:
        response = await client.get(f"/problems/{problem.arena_number}")

    assert response.status_code == 200
    assert "Sample Test Cases" in response.text
    assert ">1\n</pre>" in response.text
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
        cookies={"arena_access_token": _login_token(app, author)},
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


@pytest.mark.asyncio
async def test_problem_print_renders_statement_samples_and_limits(session: AsyncSession) -> None:
    """The print view renders a standalone page with statement, samples, and limits."""
    app = _build_problem_detail_app(session)
    author = await _create_user(
        session,
        name="Print Author",
        email="print-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    problem = await _create_enabled_problem(session, author, title="Printable Problem")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, author)},
    ) as client:
        response = await client.get(f"/problems/{problem.arena_number}/print")

    assert response.status_code == 200
    body = response.text
    assert "Resource Limits" in body
    # Author attribution is shown when the problem does not hide it.
    assert "Print Author" in body
    assert "md-statement-src" in body
    assert "data-print-page" in body
    assert "print-page.js?v=test" in body
    # Standalone shell: no sidebar / workspace resizer from the detail page.
    assert "data-problem-workspace-resizer" not in body


@pytest.mark.asyncio
async def test_problem_print_requires_authentication(session: AsyncSession) -> None:
    """The print view is gated behind login, like the problem detail page."""
    app = _build_problem_detail_app(session)
    author = await _create_user(
        session,
        name="Print Guard Author",
        email="print-guard-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    problem = await _create_enabled_problem(session, author)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(f"/problems/{problem.arena_number}/print", follow_redirects=False)

    assert response.status_code in (302, 303, 307, 401)


@pytest.mark.asyncio
async def test_public_problem_list_filters_by_statement_language(session: AsyncSession) -> None:
    """The language filter narrows the public list; garbage means "all languages"."""
    app = _build_problem_detail_app(session)
    author = await _create_user(
        session,
        name="Language Author",
        email="lang-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    portuguese = await _create_enabled_problem(session, author, title="Problema em Portugues")
    english = await _create_enabled_problem(session, author, title="Problem in English")
    portuguese.statement_language = StatementLanguage.PT
    english.statement_language = StatementLanguage.EN
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        filtered = await client.get("/problems", params={"language": "pt"})
        unfiltered = await client.get("/problems")
        bogus = await client.get("/problems", params={"language": "klingon"})

    assert filtered.status_code == 200
    assert "Problema em Portugues" in filtered.text
    assert "Problem in English" not in filtered.text
    for response in (unfiltered, bogus):
        assert response.status_code == 200
        assert "Problema em Portugues" in response.text
        assert "Problem in English" in response.text
    # The filter select renders its options and keeps the current selection.
    assert 'value="pt"' in filtered.text
    assert "All languages" in filtered.text


@pytest.mark.asyncio
async def test_language_filter_survives_paging_and_the_detail_back_link(session: AsyncSession) -> None:
    """The filter must ride along on page links and on the detail page's Back button."""
    app = _build_problem_detail_app(session)
    author = await _create_user(
        session,
        name="Paging Author",
        email="lang-paging@test.example",
        role=ArenaRole.ARENA_JUDGE,
        can_edit=True,
    )
    problems = [
        await _create_enabled_problem(session, author, title=f"Portugues {index}")
        for index in range(30)  # more than one 25-row page
    ]
    for problem in problems:
        problem.statement_language = StatementLanguage.PT
    await session.commit()
    user = await _create_user(
        session,
        name="Paging Reader",
        email="lang-paging-reader@test.example",
        role=ArenaRole.ARENA_USER,
    )
    token = _login_token(app, user)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": token},
    ) as client:
        listing = await client.get("/problems", params={"language": "pt"})
        detail = await client.get(
            f"/problems/{problems[0].arena_number}",
            params={"back_language": "pt", "back_page": "2"},
        )

    # Page links carry the active filter, and detail links carry it back.
    assert "language=pt" in listing.text
    assert "back_language=pt" in listing.text
    assert detail.status_code == 200
    back_hrefs = [part.split('"', 1)[0] for part in detail.text.split('href="')[1:] if "/problems?" in part]
    assert any("language=pt" in href and "page=2" in href for href in back_hrefs)


@pytest.mark.asyncio
async def test_problem_detail_omits_editorial_link_with_no_editorial_or_never(session: AsyncSession) -> None:
    """A problem with no editorial, or an editorial with policy never, shows no link."""
    app = _build_problem_detail_app(session)
    author = await _create_user(
        session,
        name="No Editorial Author",
        email="no-editorial-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    user = await _create_user(
        session,
        name="No Editorial User",
        email="no-editorial-user@test.example",
        role=ArenaRole.ARENA_USER,
    )
    no_editorial = await _create_enabled_problem(session, author, title="No Editorial")
    never_shown = await _create_enabled_problem(session, author, title="Never Shown Editorial")
    never_shown.editorial = "# Solution\nDo the thing."
    never_shown.editorial_release_policy = ArenaEditorialReleasePolicy.NEVER
    await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, user)},
    ) as client:
        no_editorial_response = await client.get(f"/problems/{no_editorial.arena_number}")
        never_response = await client.get(f"/problems/{never_shown.arena_number}")

    for response in (no_editorial_response, never_response):
        assert response.status_code == 200
        assert "Editorial</a>" not in response.text
        # A 'never' editorial must not even be hinted at: the page looks exactly
        # like a problem that has no editorial at all.
        assert _EDITORIAL_PENDING_HINT not in response.text


@pytest.mark.asyncio
async def test_problem_detail_shows_last_updated(session: AsyncSession) -> None:
    """The statement panel stamps the problem's last update in the viewer's timezone."""
    app = _build_problem_detail_app(session)
    author = await _create_user(
        session,
        name="Last Updated Author",
        email="last-updated-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    user = await _create_user(
        session,
        name="Last Updated User",
        email="last-updated-user@test.example",
        role=ArenaRole.ARENA_USER,
    )
    problem = await _create_enabled_problem(session, author, title="Last Updated Problem")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, user)},
    ) as client:
        response = await client.get(f"/problems/{problem.arena_number}")

    assert response.status_code == 200
    assert "Last updated at:" in response.text
    assert format_user_datetime(problem.updated_at, user) in response.text


@pytest.mark.asyncio
async def test_problem_detail_shows_editorial_link_when_always(session: AsyncSession) -> None:
    """Policy 'always' shows the editorial link and the viewer route renders the content."""
    app = _build_problem_detail_app(session)
    author = await _create_user(
        session,
        name="Always Editorial Author",
        email="always-editorial-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    user = await _create_user(
        session,
        name="Always Editorial User",
        email="always-editorial-user@test.example",
        role=ArenaRole.ARENA_USER,
    )
    problem = await _create_enabled_problem(session, author, title="Always Shown Editorial")
    problem.editorial = "# Editorial\nUse a segment tree."
    problem.editorial_release_policy = ArenaEditorialReleasePolicy.ALWAYS
    await session.commit()
    await session.refresh(problem)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, user)},
    ) as client:
        detail_response = await client.get(f"/problems/{problem.arena_number}")
        editorial_url = f"/problems/{problem.arena_number}/editorial"
        editorial_response = await client.get(editorial_url)

    assert detail_response.status_code == 200
    assert detail_response.text.count(f'href="http://testserver{editorial_url}"') == 2
    assert 'target="_blank"' in detail_response.text
    assert _EDITORIAL_PENDING_HINT not in detail_response.text
    assert editorial_response.status_code == 200
    assert "Use a segment tree." in editorial_response.text
    assert "markdown-src" in editorial_response.text


@pytest.mark.asyncio
async def test_problem_detail_gates_editorial_link_after_ac(session: AsyncSession) -> None:
    """Policy 'after_ac' hides the link until the user has an AC, and the route re-checks it."""
    app = _build_problem_detail_app(session)
    author = await _create_user(
        session,
        name="After AC Editorial Author",
        email="after-ac-editorial-author@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    user = await _create_user(
        session,
        name="After AC Editorial User",
        email="after-ac-editorial-user@test.example",
        role=ArenaRole.ARENA_USER,
    )
    problem = await _create_enabled_problem(session, author, title="After AC Editorial")
    problem.editorial = "# Editorial\nGreedy works here."
    problem.editorial_release_policy = ArenaEditorialReleasePolicy.AFTER_AC
    await session.commit()
    await session.refresh(problem)
    editorial_url = f"/problems/{problem.arena_number}/editorial"

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, user)},
    ) as client:
        before_ac_detail = await client.get(f"/problems/{problem.arena_number}")
        # A direct GET must be refused server-side even though no link was ever shown.
        before_ac_direct = await client.get(editorial_url)

    assert before_ac_detail.status_code == 200
    assert f'href="http://testserver{editorial_url}"' not in before_ac_detail.text
    # Both nav rows tell the viewer that solving unlocks the editorial.
    assert before_ac_detail.text.count(_EDITORIAL_PENDING_HINT) == 2
    assert before_ac_direct.status_code == 404

    await _mark_solved(session, problem=problem, user=user)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _login_token(app, user)},
    ) as client:
        after_ac_detail = await client.get(f"/problems/{problem.arena_number}")
        after_ac_direct = await client.get(editorial_url)

    assert after_ac_detail.status_code == 200
    assert after_ac_detail.text.count(f'href="http://testserver{editorial_url}"') == 2
    assert _EDITORIAL_PENDING_HINT not in after_ac_detail.text
    assert after_ac_direct.status_code == 200
    assert "Greedy works here." in after_ac_direct.text
