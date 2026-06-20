#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for teacher feedback on Arena submissions.

Covers:
  arena.services.arena_teacher_feedback_service.upsert_teacher_feedback
  arena.services.arena_problem_set_report_service feedback indicators
  POST /submissions/{submission_id}/teacher-feedback  (arena_submission_teacher_feedback)
  GET  /submissions/{submission_id}                   (teacher-feedback rendering)
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from _tc_helpers import make_arena_test_case
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import get_flash_service, setup_flash
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_classes  # noqa: F401
import arena.models.arena_problem_sets  # noqa: F401
import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_classes import ArenaClass
from arena.models.arena_problem_sets import ArenaProblemSet
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.routes.submissions import router as arena_submissions_router
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from arena.services.arena_problem_set_report_service import get_student_problem_submissions_for_set
from arena.services.arena_teacher_feedback_service import (
    get_teacher_feedback_text,
    upsert_teacher_feedback,
)
from arena.services.token_service import ArenaTokenAction
from arena.services.user_timezone_service import (
    datetime_local_value,
    format_user_datetime,
    timezone_name_for_user,
)
from shared.db_schema.arena import (
    arena_notifications,
    arena_submission_judgments,
    arena_submission_teacher_feedback,
    arena_submissions,
)
from shared.enumerations import ArenaNotificationKind, ArenaRole, Verdict
from web.models.language import Language

TEST_JWT_SECRET = "test-secret-key-for-teacher-feedback-tests!!"

_ARENA_DIR = Path(__file__).resolve().parents[2] / "arena"
_SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _build_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena FastAPI app for teacher-feedback route tests."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    templates = Jinja2Templates(directory=_ARENA_DIR / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["next_rating_update_text"] = lambda request: None
    templates.env.globals["arena_role_labels"] = ARENA_ROLE_DISPLAY
    templates.env.globals["verdict_badge_classes"] = {
        Verdict.AC.value: "bg-success",
        Verdict.WA.value: "bg-danger",
    }
    templates.env.globals["verdict_labels"] = {
        Verdict.AC.value: "Accepted",
        Verdict.WA.value: "Wrong Answer",
    }
    templates.env.globals["arena_datetime_local_value"] = datetime_local_value
    templates.env.globals["arena_format_datetime"] = format_user_datetime
    templates.env.globals["arena_user_timezone_name"] = timezone_name_for_user
    setup_flash(templates)
    app.state.arena_templates = templates
    app.state.arena_db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    valkey_runtime = MagicMock()
    valkey_runtime.get = AsyncMock(return_value=None)
    app.state.valkey_runtime = valkey_runtime
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

    app.mount("/static/vendor", StaticFiles(directory=_SHARED_DIR / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=_SHARED_DIR / "static" / "js"), name="static_shared_js")
    app.mount("/static/css", StaticFiles(directory=_ARENA_DIR / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=_ARENA_DIR / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=_ARENA_DIR / "static" / "img"), name="arena_static_img")

    @app.get("/auth/login", name="arena_login")
    async def _login() -> Response:
        return Response("login")

    @app.get("/auth/signup", name="arena_signup")
    async def _signup() -> Response:
        return Response("signup")

    @app.post("/auth/logout", name="arena_logout")
    async def _logout() -> Response:
        return Response("logout")

    @app.get("/", name="arena_dashboard")
    async def _dashboard() -> Response:
        return Response("dashboard")

    @app.get("/status", name="arena_status")
    async def _status() -> Response:
        return Response("status")

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

    @app.get("/user/profile/2fa/setup", name="arena_2fa_setup")
    async def _2fa_setup() -> Response:
        return Response("2fa_setup")

    @app.get("/admin/problems", name="arena_admin_problem_list")
    async def _admin_problems() -> Response:
        return Response("admin_problems")

    @app.get("/admin/users", name="arena_admin_user_list")
    async def _admin_users() -> Response:
        return Response("admin_users")

    @app.get("/admin/dashboard", name="arena_admin_dashboard")
    async def _admin_dashboard_stub() -> Response:
        return Response("dashboard")

    @app.get("/admin/categories", name="arena_admin_category_list")
    async def _admin_categories() -> Response:
        return Response("admin_categories")

    @app.get("/admin/affiliations", name="arena_admin_affiliation_list")
    async def _admin_affiliations() -> Response:
        return Response("admin_affiliations")

    @app.get("/help/rating", name="arena_help_rating")
    async def _help_rating() -> Response:
        return Response("help_rating")

    @app.get("/help/languages", name="arena_help_languages")
    async def _help_languages() -> Response:
        return Response("help_languages")

    @app.get("/ranking", name="arena_ranking_index")
    async def _ranking_index() -> Response:
        return Response("ranking")

    @app.get("/ranking/users", name="arena_ranking_users")
    async def _ranking_users() -> Response:
        return Response("ranking_users")

    @app.get("/ranking/affiliations", name="arena_ranking_affiliations")
    async def _ranking_affiliations() -> Response:
        return Response("ranking_affiliations")

    @app.get("/user/profile", name="arena_user_profile")
    async def _profile(request: Request) -> Response:
        messages = get_flash_service(request).get_flashed_messages()
        return Response("profile " + " ".join(str(m) for m in messages))

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications_list() -> Response:
        return Response("[]", media_type="application/json")

    @app.get("/user/avatar/{user_id}", name="arena_user_avatar_by_id")
    async def _user_avatar(user_id: str) -> Response:
        return Response("", status_code=204)

    @app.get("/problems/{arena_number}", name="arena_problem_detail")
    async def _problem_detail(arena_number: int) -> Response:
        return Response("problem")

    @app.get("/classes/{class_id}", name="arena_class_detail")
    async def _class_detail(class_id: str) -> Response:
        return Response("class")

    @app.get(
        "/classes/{class_id}/problem-sets/{set_id}/detail",
        name="arena_student_class_problem_set_detail",
    )
    async def _set_detail(class_id: str, set_id: str) -> Response:
        return Response("set")

    @app.get(
        "/classes/{class_id}/problem-sets/{set_id}/report/student/{user_id}",
        name="arena_class_problem_set_report_student",
    )
    async def _report_student(class_id: str, set_id: str, user_id: str) -> Response:
        return Response("report")

    app.include_router(arena_submissions_router)
    return app


# ---------------------------------------------------------------------------
# DB builders
# ---------------------------------------------------------------------------


async def _make_user(
    session: AsyncSession,
    *,
    role: ArenaRole = ArenaRole.ARENA_USER,
    prefix: str = "user",
) -> ArenaUser:
    user = ArenaUser(
        nome="Test User",
        email_normalizado=f"{prefix}_{uuid.uuid4().hex[:6]}@test.example",
        password_hash="pbkdf2:sha256:1000000$x$y",
        role=role,
        ativo=True,
        email_confirmado=True,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        com_foto=False,
        usa_2fa=False,
        precisa_trocar_senha=False,
        session_version=0,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _make_language(session: AsyncSession) -> Language:
    lang = Language(
        id=f"test-lang-{uuid.uuid4().hex[:6]}",
        name="Test Language",
        icon="devicon-test-plain",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="sol.txt",
        artifact_path="/sandbox/sol.txt",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(lang)
    await session.commit()
    return lang


async def _make_problem(session: AsyncSession, author: ArenaUser) -> ArenaProblem:
    problem = ArenaProblem(
        arena_number=int(uuid.uuid4().int % 100_000) + 1,
        title="Feedback Problem",
        owner_id=author.id,
        problem_statement="<p>Solve this.</p>",
        enabled=True,
    )
    session.add(problem)
    await session.flush()
    session.add(make_arena_test_case(problem.id, 1))
    await session.commit()
    await session.refresh(problem)
    return problem


async def _make_class(session: AsyncSession, teacher: ArenaUser) -> ArenaClass:
    today = date.today()
    arena_class = ArenaClass(
        name=f"Class {uuid.uuid4().hex[:6]}",
        teacher_id=teacher.id,
        starts_on=today - timedelta(days=10),
        finishes_on=today + timedelta(days=30),
    )
    session.add(arena_class)
    await session.commit()
    await session.refresh(arena_class)
    return arena_class


async def _make_set(session: AsyncSession, arena_class: ArenaClass) -> ArenaProblemSet:
    problem_set = ArenaProblemSet(class_id=arena_class.id, name=f"Set {uuid.uuid4().hex[:6]}")
    session.add(problem_set)
    await session.commit()
    await session.refresh(problem_set)
    return problem_set


async def _make_submission(
    session: AsyncSession,
    student: ArenaUser,
    problem: ArenaProblem,
    lang: Language,
    *,
    verdict: str | None = Verdict.WA.value,
    problem_set_id: str | None = None,
) -> str:
    sub_id = str(uuid.uuid4())
    await session.execute(
        insert(arena_submissions).values(
            id=sub_id,
            user_id=student.id,
            problem_id=problem.id,
            language_id=lang.id,
            source_code="print('x')",
            source_hash="a" * 64,
            source_size_bytes=10,
            problem_set_id=problem_set_id,
        )
    )
    await session.execute(
        insert(arena_submission_judgments).values(
            id=str(uuid.uuid4()),
            submission_id=sub_id,
            status="DONE",
            final_verdict=verdict,
            autojudge_verdict=verdict,
        )
    )
    await session.commit()
    return sub_id


def _login(client: AsyncClient, app: FastAPI, user: ArenaUser) -> None:
    token = str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )
    client.cookies.set("arena_access_token", token)


# ---------------------------------------------------------------------------
# Service: upsert_teacher_feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_inserts_then_edits(session: AsyncSession) -> None:
    """First upsert inserts the row; a second replaces text and refreshes feedback_at."""
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    arena_class = await _make_class(session, teacher)
    pset = await _make_set(session, arena_class)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=pset.id)

    first_at = await upsert_teacher_feedback(
        session, submission_id=sub_id, teacher_id=teacher.id, feedback_text="First note"
    )
    await session.commit()
    assert await get_teacher_feedback_text(session, sub_id) == "First note"

    second_at = await upsert_teacher_feedback(
        session,
        submission_id=sub_id,
        teacher_id=teacher.id,
        feedback_text="Second note",
        feedback_at=first_at + timedelta(minutes=1),
    )
    await session.commit()

    assert await get_teacher_feedback_text(session, sub_id) == "Second note"
    assert second_at > first_at
    # Still exactly one row (1:1).
    rows = (
        await session.execute(
            select(arena_submission_teacher_feedback.c.submission_id).where(
                arena_submission_teacher_feedback.c.submission_id == sub_id
            )
        )
    ).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Service: report indicators
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_feedback_indicators(session: AsyncSession) -> None:
    """Report groups expose has_feedback per entry and has_unfeedback_non_ac per group."""
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    arena_class = await _make_class(session, teacher)
    pset = await _make_set(session, arena_class)

    sub_without = await _make_submission(session, student, problem, lang, problem_set_id=pset.id)
    sub_with = await _make_submission(session, student, problem, lang, problem_set_id=pset.id)
    await upsert_teacher_feedback(session, submission_id=sub_with, teacher_id=teacher.id, feedback_text="Nice try")
    await session.commit()

    groups = await get_student_problem_submissions_for_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=pset.id,
        user_id=student.id,
    )
    assert len(groups) == 1
    group = groups[0]
    # One submission has feedback, the other does not → group still needs feedback.
    by_id = {entry.submission_id: entry for entry in group.submissions}
    assert by_id[sub_with].has_feedback is True
    assert by_id[sub_without].has_feedback is False
    assert group.has_unfeedback_non_ac is True


@pytest.mark.asyncio
async def test_report_ac_does_not_require_feedback(session: AsyncSession) -> None:
    """An AC submission without feedback does not flag the group as needing feedback."""
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    arena_class = await _make_class(session, teacher)
    pset = await _make_set(session, arena_class)
    await _make_submission(session, student, problem, lang, verdict=Verdict.AC.value, problem_set_id=pset.id)

    groups = await get_student_problem_submissions_for_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=pset.id,
        user_id=student.id,
    )
    assert groups[0].has_unfeedback_non_ac is False


@pytest.mark.asyncio
async def test_report_non_ac_with_ac_does_not_require_feedback(session: AsyncSession) -> None:
    """Non-AC submissions without feedback do not flag the group when an AC also exists."""
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    arena_class = await _make_class(session, teacher)
    pset = await _make_set(session, arena_class)
    await _make_submission(session, student, problem, lang, problem_set_id=pset.id)
    await _make_submission(session, student, problem, lang, verdict=Verdict.AC.value, problem_set_id=pset.id)
    await session.commit()

    groups = await get_student_problem_submissions_for_set(
        session,
        actor_id=teacher.id,
        actor_role=teacher.role,
        set_id=pset.id,
        user_id=student.id,
    )
    assert groups[0].has_unfeedback_non_ac is False


# ---------------------------------------------------------------------------
# POST /submissions/{submission_id}/teacher-feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_teacher_creates_feedback_and_notifies(session: AsyncSession) -> None:
    """The set's teacher saves feedback; the row is stored and the student is notified."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    arena_class = await _make_class(session, teacher)
    pset = await _make_set(session, arena_class)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=pset.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, teacher)
        resp = await client.post(
            f"/submissions/{sub_id}/teacher-feedback",
            data={
                "feedback": "Check your loop bounds.",
                "back_class_id": arena_class.id,
                "back_set_id": pset.id,
                "back_user_id": student.id,
            },
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert f"back_set_id={pset.id}" in resp.headers["location"]
    assert await get_teacher_feedback_text(session, sub_id) == "Check your loop bounds."

    notif = (
        await session.execute(
            select(arena_notifications).where(
                arena_notifications.c.user_id == student.id,
                arena_notifications.c.notification_kind == ArenaNotificationKind.TEACHER_FEEDBACK_POSTED.value,
            )
        )
    ).all()
    assert len(notif) == 1
    assert notif[0].target_url == f"/submissions/{sub_id}"
    assert str(problem.arena_number) in notif[0].message


@pytest.mark.asyncio
async def test_post_edit_creates_second_notification(session: AsyncSession) -> None:
    """Editing feedback updates the row and creates a fresh notification (history preserved)."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    arena_class = await _make_class(session, teacher)
    pset = await _make_set(session, arena_class)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=pset.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, teacher)
        await client.post(
            f"/submissions/{sub_id}/teacher-feedback",
            data={"feedback": "First"},
            follow_redirects=False,
        )
        await client.post(
            f"/submissions/{sub_id}/teacher-feedback",
            data={"feedback": "Second, expanded"},
            follow_redirects=False,
        )

    assert await get_teacher_feedback_text(session, sub_id) == "Second, expanded"
    notif = (
        await session.execute(
            select(arena_notifications.c.id).where(
                arena_notifications.c.user_id == student.id,
                arena_notifications.c.notification_kind == ArenaNotificationKind.TEACHER_FEEDBACK_POSTED.value,
            )
        )
    ).all()
    assert len(notif) == 2


@pytest.mark.asyncio
async def test_post_admin_allowed(session: AsyncSession) -> None:
    """An ARENA_ADMIN may write feedback on any set-tied non-AC submission."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    admin = await _make_user(session, role=ArenaRole.ARENA_ADMIN, prefix="admin")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    arena_class = await _make_class(session, teacher)
    pset = await _make_set(session, arena_class)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=pset.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, admin)
        resp = await client.post(
            f"/submissions/{sub_id}/teacher-feedback",
            data={"feedback": "Admin note"},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert await get_teacher_feedback_text(session, sub_id) == "Admin note"


@pytest.mark.asyncio
async def test_post_other_teacher_forbidden(session: AsyncSession) -> None:
    """A teacher who does not own the set's class gets 404 and writes nothing."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    other = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="other")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    arena_class = await _make_class(session, teacher)
    pset = await _make_set(session, arena_class)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=pset.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, other)
        resp = await client.post(
            f"/submissions/{sub_id}/teacher-feedback",
            data={"feedback": "I should not be allowed"},
            follow_redirects=False,
        )

    assert resp.status_code == 404
    assert await get_teacher_feedback_text(session, sub_id) is None


@pytest.mark.asyncio
async def test_post_other_set_id_does_not_authorize(session: AsyncSession) -> None:
    """A teacher passing a back_set_id they own that differs from the submission's set is still 404."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)

    # Teacher owns class A (and its set), but the submission is tied to another teacher's set.
    own_class = await _make_class(session, teacher)
    own_set = await _make_set(session, own_class)
    other_teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="otherT")
    other_class = await _make_class(session, other_teacher)
    other_set = await _make_set(session, other_class)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=other_set.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, teacher)
        resp = await client.post(
            f"/submissions/{sub_id}/teacher-feedback",
            data={"feedback": "Sneaky", "back_set_id": own_set.id},
            follow_redirects=False,
        )

    assert resp.status_code == 404
    assert await get_teacher_feedback_text(session, sub_id) is None


@pytest.mark.asyncio
async def test_post_ac_submission_forbidden(session: AsyncSession) -> None:
    """Feedback is rejected (404) for AC submissions."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    arena_class = await _make_class(session, teacher)
    pset = await _make_set(session, arena_class)
    sub_id = await _make_submission(session, student, problem, lang, verdict=Verdict.AC.value, problem_set_id=pset.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, teacher)
        resp = await client.post(
            f"/submissions/{sub_id}/teacher-feedback",
            data={"feedback": "AC note"},
            follow_redirects=False,
        )

    assert resp.status_code == 404
    assert await get_teacher_feedback_text(session, sub_id) is None


@pytest.mark.asyncio
async def test_post_non_set_submission_forbidden(session: AsyncSession) -> None:
    """Feedback is rejected (404) for submissions not tied to any problem set."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=None)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, teacher)
        resp = await client.post(
            f"/submissions/{sub_id}/teacher-feedback",
            data={"feedback": "Private note"},
            follow_redirects=False,
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_empty_feedback_redirects_without_write(session: AsyncSession) -> None:
    """Whitespace-only feedback redirects back with a warning and writes no row."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    arena_class = await _make_class(session, teacher)
    pset = await _make_set(session, arena_class)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=pset.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, teacher)
        resp = await client.post(
            f"/submissions/{sub_id}/teacher-feedback",
            data={"feedback": "   "},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert await get_teacher_feedback_text(session, sub_id) is None


# ---------------------------------------------------------------------------
# GET /submissions/{submission_id} — feedback rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_student_sees_feedback_label(session: AsyncSession) -> None:
    """The submission owner sees the teacher feedback text but no edit button."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    arena_class = await _make_class(session, teacher)
    pset = await _make_set(session, arena_class)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=pset.id)
    await upsert_teacher_feedback(
        session, submission_id=sub_id, teacher_id=teacher.id, feedback_text="Watch edge cases"
    )
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, student)
        resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)

    assert resp.status_code == 200
    assert "Watch edge cases" in resp.text
    assert "Teacher feedback" in resp.text
    assert "teacher-feedback-modal" not in resp.text


@pytest.mark.asyncio
async def test_get_teacher_sees_edit_button(session: AsyncSession) -> None:
    """The managing teacher viewing the student's submission sees the feedback modal trigger."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    arena_class = await _make_class(session, teacher)
    pset = await _make_set(session, arena_class)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=pset.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, teacher)
        resp = await client.get(
            f"/submissions/{sub_id}?back_class_id={arena_class.id}&back_set_id={pset.id}&back_user_id={student.id}",
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert "teacher-feedback-modal" in resp.text
    assert "Add feedback" in resp.text


@pytest.mark.asyncio
async def test_get_ac_submission_no_feedback_ui(session: AsyncSession) -> None:
    """An AC set-tied submission shows neither the feedback button nor section for the teacher."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    arena_class = await _make_class(session, teacher)
    pset = await _make_set(session, arena_class)
    sub_id = await _make_submission(session, student, problem, lang, verdict=Verdict.AC.value, problem_set_id=pset.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, teacher)
        resp = await client.get(
            f"/submissions/{sub_id}?back_class_id={arena_class.id}&back_set_id={pset.id}&back_user_id={student.id}",
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert "teacher-feedback-modal" not in resp.text
    assert "Teacher feedback" not in resp.text


@pytest.mark.asyncio
async def test_get_ac_after_rejudge_hides_existing_feedback(session: AsyncSession) -> None:
    """Feedback stored on a non-AC submission is hidden once it is rejudged to AC."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)
    arena_class = await _make_class(session, teacher)
    pset = await _make_set(session, arena_class)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=pset.id)
    await upsert_teacher_feedback(
        session, submission_id=sub_id, teacher_id=teacher.id, feedback_text="Watch edge cases"
    )
    await session.commit()

    # Simulate a rejudge that now accepts the submission: the active verdict becomes AC.
    await session.execute(
        arena_submission_judgments.update()
        .where(arena_submission_judgments.c.submission_id == sub_id)
        .values(final_verdict=Verdict.AC.value, autojudge_verdict=Verdict.AC.value)
    )
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Owner no longer sees the stale feedback text or section.
        _login(client, app, student)
        owner_resp = await client.get(f"/submissions/{sub_id}", follow_redirects=False)
        # Teacher sees no feedback section or edit modal either.
        _login(client, app, teacher)
        teacher_resp = await client.get(
            f"/submissions/{sub_id}?back_class_id={arena_class.id}&back_set_id={pset.id}&back_user_id={student.id}",
            follow_redirects=False,
        )

    assert owner_resp.status_code == 200
    assert "Watch edge cases" not in owner_resp.text
    assert "Teacher feedback" not in owner_resp.text
    assert teacher_resp.status_code == 200
    assert "teacher-feedback-modal" not in teacher_resp.text
    # The row still persists in the database; only its display is gated.
    assert await get_teacher_feedback_text(session, sub_id) == "Watch edge cases"
