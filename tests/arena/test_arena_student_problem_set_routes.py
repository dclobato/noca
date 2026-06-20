#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for Arena student problem-set list and detail pages."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from _tc_helpers import make_arena_test_case
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from werkzeug.security import generate_password_hash

import arena.models.arena_classes  # noqa: F401
import arena.models.arena_problem_set_snapshots  # noqa: F401
import arena.models.arena_problem_sets  # noqa: F401
import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_classes import ArenaClass, ArenaClassMembership
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_submissions import ArenaSubmission, ArenaSubmissionJudgment
from arena.models.arena_users import ArenaUser
from arena.routes.student_problem_sets import router as arena_student_problem_sets_router
from arena.services import arena_problem_set_service as svc
from arena.services.token_service import ArenaTokenAction
from arena.services.user_timezone_service import (
    datetime_local_value,
    format_user_datetime,
    timezone_name_for_user,
)
from shared.db_schema.arena import arena_problem_set_user_snapshots
from shared.enumerations import ArenaClassMembershipStatus, ArenaRole, Verdict

TEST_JWT_SECRET = "test-secret-key-for-student-ps-route-tests-32b!"
TODAY = date.today()


def _build_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena app for student problem-set route tests."""
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
        return Response("rating")

    @app.get("/help/languages", name="arena_help_languages")
    async def _help_languages() -> Response:
        return Response("languages")

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications() -> Response:
        return Response("[]", media_type="application/json")

    @app.get("/classes/{class_id}", name="arena_class_detail")
    async def _class_detail(class_id: str) -> Response:
        return Response(f"class {class_id}")

    app.include_router(arena_student_problem_sets_router)
    return app


async def _create_user(
    session: AsyncSession,
    *,
    name: str,
    email: str,
    role: ArenaRole,
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
        session_version=0,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _create_class(session: AsyncSession, teacher: ArenaUser, *, name: str = "Test Class") -> ArenaClass:
    """Create and flush a minimal ArenaClass owned by teacher."""
    arena_class = ArenaClass(
        name=name,
        teacher_id=teacher.id,
        starts_on=TODAY - timedelta(days=5),
        finishes_on=TODAY + timedelta(days=30),
    )
    session.add(arena_class)
    await session.flush()
    return arena_class


async def _enroll(session: AsyncSession, class_id: str, user_id: str) -> None:
    """Create an active membership for user in class."""
    session.add(
        ArenaClassMembership(
            class_id=class_id,
            user_id=user_id,
            event_date=TODAY,
            status=ArenaClassMembershipStatus.ACTIVE.value,
        )
    )
    await session.flush()


async def _create_problem(session: AsyncSession, author: ArenaUser, *, title: str) -> ArenaProblem:
    """Create and flush a minimal ArenaProblem."""
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
    await session.flush()
    return problem


def _token(app: FastAPI, user: ArenaUser) -> str:
    """Issue a login token for the given user."""
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


# --------------------------------------------------------------------------- #
# Student problem-set detail
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_student_ps_detail_requires_login(session: AsyncSession) -> None:
    teacher = await _create_user(
        session, name="Teacher", email="teacher-spsd1@test.example", role=ArenaRole.ARENA_JUDGE
    )
    arena_class = await _create_class(session, teacher)
    problem_set = await svc.create_problem_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        class_id=arena_class.id,
        name="Set A",
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            f"/classes/{arena_class.id}/my-problem-sets/{problem_set.id}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "/auth/login" in response.headers["location"]


@pytest.mark.asyncio
async def test_student_ps_detail_renders_problem_with_verdict(session: AsyncSession) -> None:
    teacher = await _create_user(
        session, name="Teacher", email="teacher-spsd2@test.example", role=ArenaRole.ARENA_JUDGE
    )
    student = await _create_user(session, name="Student", email="student-spsd2@test.example", role=ArenaRole.ARENA_USER)
    arena_class = await _create_class(session, teacher, name="Graph Theory")
    await _enroll(session, arena_class.id, student.id)
    problem = await _create_problem(session, teacher, title="Dijkstra Paths")
    problem_set = await svc.create_problem_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        class_id=arena_class.id,
        name="Week 2",
    )
    await svc.add_problems_to_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        refs=[problem.id],
    )
    # Create an AC submission tied to the problem set
    submission = ArenaSubmission(
        id=str(uuid.uuid4()),
        user_id=student.id,
        problem_id=problem.id,
        language_id=str(uuid.uuid4()),
        source_code="pass",
        source_hash="a" * 64,
        source_size_bytes=4,
        problem_set_id=problem_set.id,
    )
    session.add(submission)
    await session.flush()
    session.add(
        ArenaSubmissionJudgment(
            id=str(uuid.uuid4()),
            submission_id=submission.id,
            status="DONE",
            final_verdict=Verdict.AC.value,
        )
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, student)},
    ) as client:
        response = await client.get(f"/classes/{arena_class.id}/my-problem-sets/{problem_set.id}")

    assert response.status_code == 200
    assert "Week 2" in response.text
    assert "Dijkstra Paths" in response.text
    assert Verdict.AC.value in response.text


@pytest.mark.asyncio
async def test_student_ps_detail_shows_start_and_end_labels(session: AsyncSession) -> None:
    teacher = await _create_user(
        session, name="Teacher", email="teacher-spsd2b@test.example", role=ArenaRole.ARENA_JUDGE
    )
    student = await _create_user(
        session, name="Student", email="student-spsd2b@test.example", role=ArenaRole.ARENA_USER
    )
    arena_class = await _create_class(session, teacher, name="Dynamic Programming")
    await _enroll(session, arena_class.id, student.id)
    problem_set = await svc.create_problem_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        class_id=arena_class.id,
        name="Week 3",
    )
    await svc.set_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        starts_on=datetime.now(UTC) + timedelta(days=1),
        deadline=datetime.now(UTC) + timedelta(days=3),
        now=datetime.now(UTC),
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, student)},
    ) as client:
        response = await client.get(f"/classes/{arena_class.id}/my-problem-sets/{problem_set.id}")

    assert response.status_code == 200
    assert "Starts" in response.text
    assert "Ends" in response.text


@pytest.mark.asyncio
async def test_student_ps_detail_shows_schedule_placeholders_when_unscheduled(
    session: AsyncSession,
) -> None:
    teacher = await _create_user(
        session, name="Teacher", email="teacher-spsd2c@test.example", role=ArenaRole.ARENA_JUDGE
    )
    student = await _create_user(
        session, name="Student", email="student-spsd2c@test.example", role=ArenaRole.ARENA_USER
    )
    arena_class = await _create_class(session, teacher, name="Number Theory")
    await _enroll(session, arena_class.id, student.id)
    problem_set = await svc.create_problem_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        class_id=arena_class.id,
        name="Open Set",
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, student)},
    ) as client:
        response = await client.get(f"/classes/{arena_class.id}/my-problem-sets/{problem_set.id}")

    assert response.status_code == 200
    assert "Starts" in response.text
    assert "Ends" in response.text
    assert response.text.count("—") >= 2


@pytest.mark.asyncio
async def test_student_ps_detail_shows_rating_pending_when_no_snapshot(session: AsyncSession) -> None:
    teacher = await _create_user(
        session, name="Teacher", email="teacher-spsd3@test.example", role=ArenaRole.ARENA_JUDGE
    )
    student = await _create_user(session, name="Student", email="student-spsd3@test.example", role=ArenaRole.ARENA_USER)
    arena_class = await _create_class(session, teacher)
    await _enroll(session, arena_class.id, student.id)
    problem_set = await svc.create_problem_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        class_id=arena_class.id,
        name="Closed Set",
    )
    # Set a deadline in the past so the set is considered due
    await svc.set_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        starts_on=datetime.now(UTC) - timedelta(days=3),
        deadline=datetime.now(UTC) - timedelta(hours=1),
        now=datetime.now(UTC) - timedelta(days=4),
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, student)},
    ) as client:
        response = await client.get(f"/classes/{arena_class.id}/my-problem-sets/{problem_set.id}")

    assert response.status_code == 200
    assert "Rating computation is pending" in response.text


@pytest.mark.asyncio
async def test_student_ps_detail_shows_snapshot_rating(session: AsyncSession) -> None:
    teacher = await _create_user(
        session, name="Teacher", email="teacher-spsd4@test.example", role=ArenaRole.ARENA_JUDGE
    )
    student = await _create_user(session, name="Student", email="student-spsd4@test.example", role=ArenaRole.ARENA_USER)
    arena_class = await _create_class(session, teacher)
    await _enroll(session, arena_class.id, student.id)
    problem_set = await svc.create_problem_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        class_id=arena_class.id,
        name="Rated Set",
    )
    await svc.set_problem_set_schedule(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=problem_set.id,
        starts_on=datetime.now(UTC) - timedelta(days=3),
        deadline=datetime.now(UTC) - timedelta(hours=1),
        now=datetime.now(UTC) - timedelta(days=4),
    )
    # Insert a snapshot row for the student
    await session.execute(
        arena_problem_set_user_snapshots.insert().values(
            problem_set_id=problem_set.id,
            user_id=student.id,
            total_rating=175,
            snapshot_at=datetime.now(UTC) - timedelta(minutes=30),
        )
    )
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"arena_access_token": _token(app, student)},
    ) as client:
        response = await client.get(f"/classes/{arena_class.id}/my-problem-sets/{problem_set.id}")

    assert response.status_code == 200
    assert "175" in response.text
    assert "Results" in response.text
