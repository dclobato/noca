#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for Arena class browsing and management."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from _tc_helpers import make_arena_test_case
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import FlashCategory, setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from werkzeug.security import generate_password_hash

import arena.models.arena_classes  # noqa: F401
import arena.models.arena_problem_sets  # noqa: F401
import arena.models.arena_problems  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_affiliations import ArenaAffiliation
from arena.models.arena_classes import ArenaClassMembership, ArenaClassRegistrationRequest
from arena.models.arena_problem_sets import ArenaProblemSet
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.routes.classes import router as arena_classes_router
from arena.routes.classes_members import (
    class_members_add,
)
from arena.routes.classes_members import (
    router as arena_classes_members_router,
)
from arena.routes.problem_sets import class_problem_set_problem_add
from arena.routes.problem_sets import router as arena_problem_sets_router
from arena.routes.problem_sets_autocomplete import router as arena_problem_sets_autocomplete_router
from arena.routes.problem_sets_report import router as arena_problem_sets_report_router
from arena.services import arena_problem_set_service
from arena.services.arena_class_service import create_class
from arena.services.token_service import ArenaTokenAction
from arena.services.user_timezone_service import (
    datetime_local_value,
    format_user_datetime,
    timezone_name_for_user,
)
from shared.db_schema.arena import (
    arena_class_memberships,
    arena_notifications,
    arena_problem_ratings,
    arena_problem_set_problems,
)
from shared.enumerations import ArenaClassMembershipStatus, ArenaNotificationKind, ArenaRole, Verdict

TEST_JWT_SECRET = "test-secret-key-for-class-route-tests-32b!"
TODAY = date.today()


def _mock_email_svc() -> MagicMock:
    """Return a fresh mock EmailService that always reports success."""
    return MagicMock(send_email=MagicMock(return_value=MagicMock(success=True)))


def _build_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app for class route tests."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    root_dir = Path(__file__).resolve().parents[2]
    arena_dir = root_dir / "arena"
    shared_dir = root_dir / "shared"
    templates = Jinja2Templates(directory=arena_dir / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["next_rating_update_text"] = lambda request: None
    templates.env.globals["token_expiry_text"] = lambda request: None
    templates.env.globals["verdict_badge_classes"] = {Verdict.AC.value: "bg-success", Verdict.WA.value: "bg-danger"}
    templates.env.globals["verdict_labels"] = {Verdict.AC.value: "Accepted", Verdict.WA.value: "Wrong Answer"}
    templates.env.globals["arena_datetime_local_value"] = datetime_local_value
    templates.env.globals["arena_format_datetime"] = format_user_datetime
    templates.env.globals["arena_user_timezone_name"] = timezone_name_for_user
    setup_flash(templates)
    app.state.arena_templates = templates
    app.state.arena_db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.state.email_service = _mock_email_svc()
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

    @app.get("/user/{user_id}/avatar", name="arena_user_avatar_by_id")
    async def _avatar(user_id: str) -> Response:
        return Response("avatar", media_type="image/svg+xml")

    @app.get("/admin/problems", name="arena_admin_problem_list")
    async def _admin_problems() -> Response:
        return Response("admin problems")

    @app.get("/admin/users", name="arena_admin_user_list")
    async def _admin_users() -> Response:
        return Response("admin users")

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
    async def _problems() -> Response:
        return Response("problems")

    @app.get("/problems/{arena_number}", name="arena_problem_detail")
    async def _problem_detail(arena_number: int) -> Response:
        return Response(f"problem {arena_number}")

    @app.get("/ranking", name="arena_ranking_index")
    async def _ranking() -> Response:
        return Response("ranking")

    @app.get("/help/rating", name="arena_help_rating")
    async def _help_rating() -> Response:
        return Response("rating")

    @app.get("/help/languages", name="arena_help_languages")
    async def _help_languages() -> Response:
        return Response("languages")

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications() -> Response:
        return Response("[]", media_type="application/json")

    app.include_router(arena_classes_router)
    app.include_router(arena_classes_members_router)
    app.include_router(arena_problem_sets_router)
    app.include_router(arena_problem_sets_report_router)
    app.include_router(arena_problem_sets_autocomplete_router)
    return app


async def _create_user(
    session: AsyncSession,
    *,
    name: str,
    email: str,
    role: ArenaRole,
    affiliation_id: str | None = None,
) -> ArenaUser:
    """Create and commit a minimal Arena user."""
    user = ArenaUser(
        nome=name,
        email_normalizado=email,
        password_hash=generate_password_hash("StrongPass1!"),
        role=role,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        affiliation_id=affiliation_id,
        session_version=0,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _create_problem(
    session: AsyncSession, author: ArenaUser, *, title: str, rating: int | None = None
) -> ArenaProblem:
    problem = ArenaProblem(
        arena_number=int(uuid.uuid4().int % 1_000_000) + 1,
        title=title,
        owner_id=author.id,
        enabled=True,
        problem_statement="<p>Statement</p>",
    )
    session.add(problem)
    await session.flush()
    session.add(make_arena_test_case(problem.id, 1))
    if rating is not None:
        await session.execute(arena_problem_ratings.insert().values(problem_id=problem.id, rating=rating))
    await session.flush()
    return problem


async def _enroll(session: AsyncSession, class_id: str, user_id: str) -> None:
    session.add(
        ArenaClassMembership(
            class_id=class_id,
            user_id=user_id,
            event_date=TODAY,
            status=ArenaClassMembershipStatus.ACTIVE.value,
        )
    )
    await session.flush()


class _FormData:
    def __init__(self, values: dict[str, list[str]]) -> None:
        self._values = values

    def getlist(self, key: str) -> list[str]:
        return self._values.get(key, [])


class _ProblemSetAddRequest:
    def __init__(self, problem_refs: list[str]) -> None:
        self._form = _FormData({"problem_refs": problem_refs})

    async def form(self) -> _FormData:
        return self._form

    def url_for(self, name: str, **path_params: str) -> str:
        if name != "arena_class_problem_set_manage":
            raise AssertionError(f"Unexpected route name: {name}")
        return f"/classes/{path_params['class_id']}/problem-sets/{path_params['set_id']}/problems"


class _ClassMembersAddRequest:
    def __init__(self, student_ids: list[str]) -> None:
        self._form = _FormData({"student_ids": student_ids})
        self.app = MagicMock()
        self.app.state.email_service = _mock_email_svc()

    async def form(self) -> _FormData:
        return self._form

    def url_for(self, name: str, **path_params: str) -> str:
        if name == "arena_class_members":
            return f"/classes/{path_params['class_id']}/members"
        if name == "arena_class_detail":
            return f"/classes/{path_params['class_id']}"
        raise AssertionError(f"Unexpected route name: {name}")


async def _create_affiliation(session: AsyncSession, name: str = "University") -> ArenaAffiliation:
    """Create and commit an Arena affiliation."""
    affiliation = ArenaAffiliation(id=str(uuid.uuid4()), name=name)
    session.add(affiliation)
    await session.commit()
    await session.refresh(affiliation)
    return affiliation


def _token(app: FastAPI, user: ArenaUser) -> str:
    """Issue a login token for route tests."""
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


@pytest.mark.asyncio
async def test_class_list_requires_login(session: AsyncSession) -> None:
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/classes", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("http://testserver/auth/login")


@pytest.mark.asyncio
async def test_class_list_renders_open_classes_for_user_affiliation(session: AsyncSession) -> None:
    affiliation = await _create_affiliation(session)
    judge = await _create_user(
        session,
        name="Teacher",
        email="teacher@test.example",
        role=ArenaRole.ARENA_JUDGE,
        affiliation_id=affiliation.id,
    )
    user = await _create_user(
        session,
        name="Student",
        email="student@test.example",
        role=ArenaRole.ARENA_USER,
        affiliation_id=affiliation.id,
    )
    await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Algorithms",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=3),
        allow_self_registration=True,
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, user)},
    ) as client:
        response = await client.get("/classes/open")

    assert response.status_code == 200
    assert "Algorithms" in response.text
    assert "Ask registration" in response.text


@pytest.mark.asyncio
async def test_judge_manage_tab_and_new_class_form_render(session: AsyncSession) -> None:
    judge = await _create_user(
        session,
        name="Judge",
        email="judge@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Judge Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=3),
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, judge)},
    ) as client:
        list_response = await client.get("/classes/manage")
        form_response = await client.get("/classes/new")

    assert list_response.status_code == 200
    assert "Judge Class" in list_response.text
    assert "Add new class" in form_response.text


@pytest.mark.asyncio
async def test_teacher_autocomplete_filters_for_user_and_lists_all_for_admin(session: AsyncSession) -> None:
    admin = await _create_user(
        session,
        name="Admin",
        email="admin@test.example",
        role=ArenaRole.ARENA_ADMIN,
    )
    judge = await _create_user(
        session,
        name="Autocomplete Judge",
        email="judge-search@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    user = await _create_user(
        session,
        name="Plain",
        email="plain@test.example",
        role=ArenaRole.ARENA_USER,
    )
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", _token(app, user))
        user_response = await client.get("/classes/teachers/autocomplete?q=Judge")
        client.cookies.set("arena_access_token", _token(app, admin))
        admin_response = await client.get("/classes/teachers/autocomplete?q=Judge")

    assert user_response.status_code == 200
    assert user_response.json() == {"teachers": []}
    assert admin_response.status_code == 200
    assert admin_response.json() == {
        "teachers": [{"id": judge.id, "label": "Autocomplete Judge <judge-search@test.example>"}]
    }


@pytest.mark.asyncio
async def test_class_members_page_includes_pending_student_picker(session: AsyncSession) -> None:
    judge = await _create_user(
        session,
        name="Judge Members",
        email="judge-members@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    student = await _create_user(
        session,
        name="Student Candidate",
        email="student-candidate@test.example",
        role=ArenaRole.ARENA_USER,
    )
    member = await _create_user(
        session,
        name="Enrolled Student",
        email="enrolled-student@test.example",
        role=ArenaRole.ARENA_USER,
    )
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Class Members",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=10),
    )
    await _enroll(session, arena_class.id, member.id)
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, judge)},
    ) as client:
        page_response = await client.get(f"/classes/{arena_class.id}/members")
        autocomplete_response = await client.get(f"/classes/{arena_class.id}/members/autocomplete?q=Candidate")

    assert page_response.status_code == 200
    assert "data-student-autocomplete" in page_response.text
    assert "data-student-pending-list" in page_response.text
    assert "Add students" in page_response.text
    assert f"/user/{member.id}/avatar" in page_response.text
    assert 'width="32"' in page_response.text
    assert 'height="32"' in page_response.text
    assert "Enrolled Student" in page_response.text
    assert autocomplete_response.status_code == 200
    assert autocomplete_response.json() == {
        "students": [{"id": student.id, "label": "Student Candidate <student-candidate@test.example>"}]
    }


@pytest.mark.asyncio
async def test_class_members_add_accepts_pending_student_ids(session: AsyncSession) -> None:
    judge = await _create_user(
        session,
        name="Judge Add",
        email="judge-add-members@test.example",
        role=ArenaRole.ARENA_JUDGE,
    )
    first_student = await _create_user(
        session,
        name="First Add",
        email="first-add@test.example",
        role=ArenaRole.ARENA_USER,
    )
    second_student = await _create_user(
        session,
        name="Second Add",
        email="second-add@test.example",
        role=ArenaRole.ARENA_USER,
    )
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Class Add",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=10),
    )
    await session.commit()

    flashed: list[tuple[str, FlashCategory]] = []

    def flash(message: str, category: FlashCategory) -> None:
        flashed.append((message, category))

    response = await class_members_add(
        _ClassMembersAddRequest([first_student.id, second_student.id]),
        class_id=arena_class.id,
        flash=flash,
        current_user=judge,
        session=session,
    )

    assert response.status_code == 303
    assert flashed == [("2 students added.", FlashCategory.SUCCESS)]
    stored_rows = await session.execute(
        select(arena_class_memberships.c.user_id).where(
            arena_class_memberships.c.class_id == arena_class.id,
            arena_class_memberships.c.status == ArenaClassMembershipStatus.ACTIVE.value,
        )
    )
    assert {row[0] for row in stored_rows.all()} == {first_student.id, second_student.id}


@pytest.mark.asyncio
async def test_manage_tab_includes_problem_set_button(session: AsyncSession) -> None:
    judge = await _create_user(session, name="Judge", email="judge-ps@test.example", role=ArenaRole.ARENA_JUDGE)
    await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Problem Set Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=10),
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, judge)},
    ) as client:
        response = await client.get("/classes/manage")

    assert response.status_code == 200
    assert "Manage problem sets" in response.text


@pytest.mark.asyncio
async def test_problem_set_list_page_renders_rows(session: AsyncSession) -> None:
    judge = await _create_user(session, name="Judge", email="judge-list@test.example", role=ArenaRole.ARENA_JUDGE)
    student = await _create_user(session, name="Student", email="student-list@test.example", role=ArenaRole.ARENA_USER)
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Class A",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=10),
    )
    await _enroll(session, arena_class.id, student.id)
    problem_set = await arena_problem_set_service.create_problem_set(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        class_id=arena_class.id,
        name="Week 1",
        description="Intro list",
    )
    await arena_problem_set_service.set_problem_set_schedule(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        set_id=problem_set.id,
        starts_on=datetime(TODAY.year, TODAY.month, TODAY.day, 12, 0, tzinfo=UTC),
        deadline=datetime(TODAY.year, TODAY.month, TODAY.day, 12, 0, tzinfo=UTC) + timedelta(days=1),
        now=datetime(TODAY.year, TODAY.month, TODAY.day, 11, 0, tzinfo=UTC),
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, judge)},
    ) as client:
        response = await client.get(f"/classes/{arena_class.id}/problem-sets")

    assert response.status_code == 200
    assert "Week 1" in response.text
    assert "Intro list" in response.text
    assert "Add new problem set" in response.text


@pytest.mark.asyncio
async def test_problem_set_manage_and_report_pages_render(session: AsyncSession) -> None:
    judge = await _create_user(session, name="Judge", email="judge-manage@test.example", role=ArenaRole.ARENA_JUDGE)
    student = await _create_user(
        session, name="Student", email="student-manage@test.example", role=ArenaRole.ARENA_USER
    )
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Class B",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=10),
    )
    await _enroll(session, arena_class.id, student.id)
    problem = await _create_problem(session, judge, title="Binary Search", rating=50)
    problem_set = await arena_problem_set_service.create_problem_set(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        class_id=arena_class.id,
        name="Week 2",
        description="Original teacher notes",
    )
    await arena_problem_set_service.add_problems_to_set(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        set_id=problem_set.id,
        refs=[problem.id],
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, judge)},
    ) as client:
        manage_response = await client.get(f"/classes/{arena_class.id}/problem-sets/{problem_set.id}/problems")
        report_response = await client.get(f"/classes/{arena_class.id}/problem-sets/{problem_set.id}/report")
        autocomplete_response = await client.get(
            f"/classes/{arena_class.id}/problem-sets/{problem_set.id}/problems/autocomplete?q=Binary"
        )

    assert manage_response.status_code == 200
    assert "Manage Problems" in manage_response.text
    assert "Difficulty" in manage_response.text
    assert "title, and difficulty" in manage_response.text
    assert "Binary Search" in manage_response.text
    assert 'name="description"' in manage_response.text
    assert "Original teacher notes" in manage_response.text
    assert "data-problem-ref-list" in manage_response.text
    assert "data-problem-pending-list" in manage_response.text
    assert "Add problems" in manage_response.text
    assert 'name="problem_ref"' not in manage_response.text
    assert report_response.status_code == 200
    assert "Problem Set Report" in report_response.text
    assert "Student" in report_response.text
    assert f"/user/{student.id}/avatar" in report_response.text
    assert 'width="32"' in report_response.text
    assert 'height="32"' in report_response.text
    assert autocomplete_response.status_code == 200
    assert autocomplete_response.json()["problems"] == []


@pytest.mark.asyncio
async def test_problem_set_add_accepts_pending_problem_refs(session: AsyncSession) -> None:
    judge = await _create_user(session, name="Judge", email="judge-pending@test.example", role=ArenaRole.ARENA_JUDGE)
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Class Pending",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=10),
    )
    first_problem = await _create_problem(session, judge, title="First Pending")
    second_problem = await _create_problem(session, judge, title="Second Pending")
    problem_set = await arena_problem_set_service.create_problem_set(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        class_id=arena_class.id,
        name="Week Pending",
    )
    await session.commit()

    flashed: list[tuple[str, FlashCategory]] = []

    def flash(message: str, category: FlashCategory) -> None:
        flashed.append((message, category))

    response = await class_problem_set_problem_add(
        _ProblemSetAddRequest([str(first_problem.arena_number), str(second_problem.arena_number)]),
        class_id=arena_class.id,
        set_id=problem_set.id,
        flash=flash,
        current_user=judge,
        session=session,
    )

    assert response.status_code == 303
    assert flashed == [("Problems added to the problem set.", FlashCategory.SUCCESS)]
    stored_rows = await session.execute(
        select(arena_problem_set_problems.c.problem_id).where(
            arena_problem_set_problems.c.problem_set_id == problem_set.id
        )
    )
    assert {row[0] for row in stored_rows.all()} == {first_problem.id, second_problem.id}


@pytest.mark.asyncio
async def test_problem_set_delete_requires_correct_password(session: AsyncSession) -> None:
    judge = await _create_user(session, name="Judge", email="judge-delete@test.example", role=ArenaRole.ARENA_JUDGE)
    arena_class = await create_class(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        name="Class C",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=10),
    )
    problem_set = await arena_problem_set_service.create_problem_set(
        session,
        actor_id=judge.id,
        actor_role=judge.role,
        class_id=arena_class.id,
        name="To Delete",
    )
    problem_set_id = problem_set.id
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, judge)},
    ) as client:
        bad_response = await client.post(
            f"/classes/{arena_class.id}/problem-sets/{problem_set_id}/delete",
            data={"password": "wrong", "page": "1", "sort": "deadline", "direction": "desc"},
            follow_redirects=False,
        )
        good_response = await client.post(
            f"/classes/{arena_class.id}/problem-sets/{problem_set_id}/delete",
            data={"password": "StrongPass1!", "page": "1", "sort": "deadline", "direction": "desc"},
            follow_redirects=False,
        )

    assert bad_response.status_code == 303
    assert good_response.status_code == 303
    session.expire_all()
    assert await session.get(ArenaProblemSet, problem_set_id) is None


@pytest.mark.asyncio
async def test_class_detail_allows_admin_teacher_active_member_and_self_reg(session: AsyncSession) -> None:
    teacher = await _create_user(session, name="Teacher", email="teacher-cd@test.example", role=ArenaRole.ARENA_JUDGE)
    admin = await _create_user(session, name="Admin", email="admin-cd@test.example", role=ArenaRole.ARENA_ADMIN)
    member = await _create_user(session, name="Member", email="member-cd@test.example", role=ArenaRole.ARENA_USER)
    outsider = await _create_user(session, name="Outsider", email="outsider-cd@test.example", role=ArenaRole.ARENA_USER)
    arena_class = await create_class(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        name="Detail Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=10),
    )
    await _enroll(session, arena_class.id, member.id)
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as public_client:
        no_auth = await public_client.get(f"/classes/{arena_class.id}")
    assert no_auth.status_code == 303

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, admin)},
    ) as admin_client:
        admin_resp = await admin_client.get(f"/classes/{arena_class.id}")
    assert admin_resp.status_code == 200

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, teacher)},
    ) as teacher_client:
        teacher_resp = await teacher_client.get(f"/classes/{arena_class.id}")
    assert teacher_resp.status_code == 200

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, member)},
    ) as member_client:
        member_resp = await member_client.get(f"/classes/{arena_class.id}")
    assert member_resp.status_code == 200

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, outsider)},
    ) as outsider_client:
        outsider_resp = await outsider_client.get(f"/classes/{arena_class.id}")
    assert outsider_resp.status_code == 403


@pytest.mark.asyncio
async def test_class_detail_allows_prospective_student_when_self_registration_open(
    session: AsyncSession,
) -> None:
    teacher = await _create_user(session, name="Teacher", email="teacher-open@test.example", role=ArenaRole.ARENA_JUDGE)
    prospective = await _create_user(
        session, name="Prospective", email="prospective@test.example", role=ArenaRole.ARENA_USER
    )
    member = await _create_user(
        session,
        name="Registered",
        email="registered-open@test.example",
        role=ArenaRole.ARENA_USER,
    )
    admin_member = await _create_user(
        session,
        name="Registered Admin",
        email="registered-admin-open@test.example",
        role=ArenaRole.ARENA_ADMIN,
    )
    arena_class = await create_class(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        name="Open Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=10),
        allow_self_registration=True,
    )
    await _enroll(session, arena_class.id, member.id)
    await _enroll(session, arena_class.id, admin_member.id)
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, prospective)},
    ) as client:
        response = await client.get(f"/classes/{arena_class.id}")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, member)},
    ) as client:
        member_response = await client.get(f"/classes/{arena_class.id}")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, admin_member)},
    ) as client:
        admin_member_response = await client.get(f"/classes/{arena_class.id}")

    assert response.status_code == 200
    assert "Open Class" in response.text
    assert "Ask registration" in response.text
    assert member_response.status_code == 200
    assert "Ask registration" not in member_response.text
    assert "Registered" in member_response.text
    assert admin_member_response.status_code == 200
    assert "Ask registration" not in admin_member_response.text
    assert "Registered" in admin_member_response.text


@pytest.mark.asyncio
async def test_class_detail_forbids_prospective_student_when_self_registration_closed(
    session: AsyncSession,
) -> None:
    teacher = await _create_user(
        session, name="Teacher2", email="teacher-closed@test.example", role=ArenaRole.ARENA_JUDGE
    )
    prospective = await _create_user(
        session, name="Prospective2", email="prospective2@test.example", role=ArenaRole.ARENA_USER
    )
    arena_class = await create_class(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        name="Closed Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=10),
        allow_self_registration=False,
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, prospective)},
    ) as client:
        response = await client.get(f"/classes/{arena_class.id}")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Email notification route tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registration_request_sends_teacher_email(session: AsyncSession) -> None:
    """POST /classes/{id}/request-registration notifies the teacher by email."""
    teacher = await _create_user(
        session, name="Email Teacher", email="email-teacher@test.example", role=ArenaRole.ARENA_JUDGE
    )
    student = await _create_user(
        session, name="Email Student", email="email-student@test.example", role=ArenaRole.ARENA_USER
    )
    arena_class = await create_class(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        name="Email Request Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
        allow_self_registration=True,
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, student)},
    ) as client:
        response = await client.post(
            f"/classes/{arena_class.id}/request-registration",
            follow_redirects=False,
        )

    assert response.status_code == 303
    app.state.email_service.send_email.assert_called_once()
    call = app.state.email_service.send_email.call_args
    assert call.kwargs["to_email"] == teacher.email_normalizado
    assert "Email Request Class" in call.kwargs["subject"]
    assert "Email Student" in call.kwargs["text_body"]

    notifs = (
        (
            await session.execute(
                select(arena_notifications.c.notification_kind).where(
                    arena_notifications.c.user_id == teacher.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert ArenaNotificationKind.CLASS_REGISTRATION_REQUEST.value in notifs


@pytest.mark.asyncio
async def test_registration_approval_sends_student_email(session: AsyncSession) -> None:
    """POST /classes/registration-requests/{id}/approve sends an email to the student."""
    teacher = await _create_user(
        session, name="Approve Teacher", email="approve-teacher@test.example", role=ArenaRole.ARENA_JUDGE
    )
    student = await _create_user(
        session, name="Approve Student", email="approve-student@test.example", role=ArenaRole.ARENA_USER
    )
    arena_class = await create_class(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        name="Approve Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
        allow_self_registration=True,
    )
    reg = ArenaClassRegistrationRequest(
        class_id=arena_class.id,
        user_id=student.id,
        status="PENDING",
    )
    session.add(reg)
    await session.commit()
    await session.refresh(reg)

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, teacher)},
    ) as client:
        response = await client.post(
            f"/classes/registration-requests/{reg.id}/approve",
            data={"class_id": arena_class.id},
            follow_redirects=False,
        )

    assert response.status_code == 303
    app.state.email_service.send_email.assert_called_once()
    call = app.state.email_service.send_email.call_args
    assert call.kwargs["to_email"] == student.email_normalizado
    assert "approved" in call.kwargs["subject"].lower()
    assert "Approve Class" in call.kwargs["subject"]

    # verify in-app notification was also created
    notifs = (
        (
            await session.execute(
                select(arena_notifications.c.notification_kind).where(
                    arena_notifications.c.user_id == student.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert ArenaNotificationKind.CLASS_REGISTRATION_APPROVED.value in notifs


@pytest.mark.asyncio
async def test_registration_denial_sends_student_email_with_reason(session: AsyncSession) -> None:
    """POST /classes/registration-requests/{id}/deny sends an email with the denial reason."""
    teacher = await _create_user(
        session, name="Deny Teacher", email="deny-teacher@test.example", role=ArenaRole.ARENA_JUDGE
    )
    student = await _create_user(
        session, name="Deny Student", email="deny-student@test.example", role=ArenaRole.ARENA_USER
    )
    arena_class = await create_class(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        name="Deny Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
        allow_self_registration=True,
    )
    reg = ArenaClassRegistrationRequest(
        class_id=arena_class.id,
        user_id=student.id,
        status="PENDING",
    )
    session.add(reg)
    await session.commit()
    await session.refresh(reg)

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, teacher)},
    ) as client:
        response = await client.post(
            f"/classes/registration-requests/{reg.id}/deny",
            data={"class_id": arena_class.id, "reason": "Class is full"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    app.state.email_service.send_email.assert_called_once()
    call = app.state.email_service.send_email.call_args
    assert call.kwargs["to_email"] == student.email_normalizado
    assert "denied" in call.kwargs["subject"].lower()
    assert "Class is full" in call.kwargs["text_body"]

    notifs = (
        (
            await session.execute(
                select(arena_notifications.c.notification_kind).where(
                    arena_notifications.c.user_id == student.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert ArenaNotificationKind.CLASS_REGISTRATION_DENIED.value in notifs


@pytest.mark.asyncio
async def test_direct_add_creates_membership_added_notification_and_email(session: AsyncSession) -> None:
    """POST /classes/{id}/members sends CLASS_MEMBERSHIP_ADDED notification and email per user."""
    teacher = await _create_user(
        session, name="Add Teacher", email="add-teacher@test.example", role=ArenaRole.ARENA_JUDGE
    )
    student = await _create_user(
        session, name="Add Student", email="add-student@test.example", role=ArenaRole.ARENA_USER
    )
    arena_class = await create_class(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        name="Direct Add Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, teacher)},
    ) as client:
        response = await client.post(
            f"/classes/{arena_class.id}/members",
            data={"student_ids": student.id},
            follow_redirects=False,
        )

    assert response.status_code == 303
    app.state.email_service.send_email.assert_called_once()
    call = app.state.email_service.send_email.call_args
    assert call.kwargs["to_email"] == student.email_normalizado
    assert "Direct Add Class" in call.kwargs["subject"]

    notifs = (
        (
            await session.execute(
                select(arena_notifications.c.notification_kind).where(
                    arena_notifications.c.user_id == student.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert ArenaNotificationKind.CLASS_MEMBERSHIP_ADDED.value in notifs


@pytest.mark.asyncio
async def test_member_remove_sends_removed_email(session: AsyncSession) -> None:
    """POST /classes/{id}/members/{uid}/remove sends a removal email when teacher removes student."""
    teacher = await _create_user(
        session, name="Remove Teacher", email="remove-teacher@test.example", role=ArenaRole.ARENA_JUDGE
    )
    student = await _create_user(
        session, name="Remove Student", email="remove-student@test.example", role=ArenaRole.ARENA_USER
    )
    arena_class = await create_class(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        name="Remove Email Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
    )
    await _enroll(session, arena_class.id, student.id)
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, teacher)},
    ) as client:
        response = await client.post(
            f"/classes/{arena_class.id}/members/{student.id}/remove",
            follow_redirects=False,
        )

    assert response.status_code == 303
    app.state.email_service.send_email.assert_called_once()
    call = app.state.email_service.send_email.call_args
    assert call.kwargs["to_email"] == student.email_normalizado
    assert "Remove Email Class" in call.kwargs["subject"]
    assert "removed" in call.kwargs["text_body"].lower()

    notifs = (
        (
            await session.execute(
                select(arena_notifications.c.notification_kind).where(
                    arena_notifications.c.user_id == student.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert ArenaNotificationKind.CLASS_MEMBERSHIP_REMOVED.value in notifs


@pytest.mark.asyncio
async def test_self_removal_does_not_send_email(session: AsyncSession) -> None:
    """Self-removal keeps existing no-notification behavior — no email sent."""
    teacher = await _create_user(
        session, name="Self Remove Teacher", email="self-remove-teacher@test.example", role=ArenaRole.ARENA_JUDGE
    )
    student = await _create_user(
        session, name="Self Remove Student", email="self-remove-student@test.example", role=ArenaRole.ARENA_USER
    )
    arena_class = await create_class(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        name="Self Remove Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
    )
    await _enroll(session, arena_class.id, student.id)
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, student)},
    ) as client:
        response = await client.post(
            f"/classes/{arena_class.id}/members/{student.id}/remove",
            follow_redirects=False,
        )

    assert response.status_code == 303
    app.state.email_service.send_email.assert_not_called()


@pytest.mark.asyncio
async def test_email_failure_after_commit_does_not_undo_membership(session: AsyncSession) -> None:
    """A failing email send after commit leaves the membership change durable."""
    teacher = await _create_user(
        session, name="Fail Teacher", email="fail-teacher@test.example", role=ArenaRole.ARENA_JUDGE
    )
    student = await _create_user(
        session, name="Fail Student", email="fail-student@test.example", role=ArenaRole.ARENA_USER
    )
    arena_class = await create_class(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        name="Fail Email Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
    )
    await session.commit()

    app = _build_app(session)
    app.state.email_service.send_email.side_effect = RuntimeError("SMTP down")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, teacher)},
    ) as client:
        response = await client.post(
            f"/classes/{arena_class.id}/members",
            data={"student_ids": student.id},
            follow_redirects=False,
        )

    assert response.status_code == 303
    class_id = arena_class.id
    student_id = student.id
    session.expire_all()
    stored = (
        (
            await session.execute(
                select(arena_class_memberships.c.user_id).where(
                    arena_class_memberships.c.class_id == class_id,
                    arena_class_memberships.c.status == ArenaClassMembershipStatus.ACTIVE.value,
                )
            )
        )
        .scalars()
        .all()
    )
    assert student_id in stored

    notifs = (
        (
            await session.execute(
                select(arena_notifications.c.notification_kind).where(
                    arena_notifications.c.user_id == student_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert ArenaNotificationKind.CLASS_MEMBERSHIP_ADDED.value in notifs


@pytest.mark.asyncio
async def test_direct_add_deduplicates_student_ids(session: AsyncSession) -> None:
    """Submitting duplicate student_ids sends exactly one email and one notification per student."""
    teacher = await _create_user(
        session, name="Dedup Teacher", email="dedup-teacher@test.example", role=ArenaRole.ARENA_JUDGE
    )
    student = await _create_user(
        session, name="Dedup Student", email="dedup-student@test.example", role=ArenaRole.ARENA_USER
    )
    arena_class = await create_class(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        name="Dedup Class",
        starts_on=TODAY,
        finishes_on=TODAY + timedelta(days=5),
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, teacher)},
    ) as client:
        response = await client.post(
            f"/classes/{arena_class.id}/members",
            data={"student_ids": [str(student.id), str(student.id)]},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert app.state.email_service.send_email.call_count == 1

    notifs = (
        (
            await session.execute(
                select(arena_notifications.c.notification_kind).where(
                    arena_notifications.c.user_id == student.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert notifs.count(ArenaNotificationKind.CLASS_MEMBERSHIP_ADDED.value) == 1
