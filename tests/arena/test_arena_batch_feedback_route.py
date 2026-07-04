#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route-level tests for POST /classes/{class_id}/problem-sets/{set_id}/problems/{problem_id}/batch-feedback.

Covers the authorization boundaries a unit test on the service layer alone cannot
exercise end-to-end: the URL's class_id/set_id consistency check and the actual
HTTP-level rejection paths, plus the unchanged-feedback skip and notification count.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from _tc_helpers import make_arena_test_case
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
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
from arena.models.arena_classes import ArenaClass, ArenaClassMembership
from arena.models.arena_problem_sets import ArenaProblemSet
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.routes.problem_sets_batch_feedback import router as batch_feedback_router
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from arena.services.arena_teacher_feedback_service import get_teacher_feedback_text, upsert_teacher_feedback
from arena.services.token_service import ArenaTokenAction
from shared.db_schema.arena import (
    arena_notifications,
    arena_problem_set_problems,
    arena_submission_ai_reviews,
    arena_submission_judgments,
    arena_submission_test_results,
    arena_submissions,
    arena_test_cases,
)
from shared.enumerations import ArenaClassMembershipStatus, ArenaNotificationKind, ArenaRole, Verdict
from web.models.language import Language

TEST_JWT_SECRET = "test-secret-key-for-batch-feedback-tests!!"

_ARENA_DIR = Path(__file__).resolve().parents[2] / "arena"
_SHARED_DIR = Path(__file__).resolve().parents[2] / "shared"


def _build_app(session: AsyncSession) -> FastAPI:
    """Build a minimal Arena FastAPI app for batch-feedback route tests."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")

    templates = Jinja2Templates(directory=_ARENA_DIR / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["next_rating_update_text"] = lambda request: None
    templates.env.globals["arena_role_labels"] = ARENA_ROLE_DISPLAY
    templates.env.globals["verdict_badge_classes"] = {Verdict.WA.value: "bg-danger"}
    templates.env.globals["verdict_labels"] = {Verdict.WA.value: "Wrong Answer"}
    templates.env.globals["arena_format_datetime"] = lambda value, user, fmt="%Y-%m-%d %H:%M": value.strftime(fmt)
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

    async def _stub() -> Response:
        return Response("stub")

    @app.get("/auth/login", name="arena_login")
    async def _login_stub() -> Response:
        return Response("login")

    @app.get("/stub/avatar/{user_id}", name="arena_user_avatar_by_id")
    async def _avatar_stub(user_id: str) -> Response:
        return Response(user_id)

    @app.get(
        "/classes/{class_id}/problem-sets/{set_id}/problems",
        name="arena_class_problem_set_manage",
    )
    async def _manage_stub(class_id: str, set_id: str) -> Response:
        return Response("manage")

    for route_name in (
        "arena_dashboard",
        "arena_notifications_list",
        "arena_user_profile",
        "arena_problem_list",
        "arena_classes_index",
        "arena_classes_registered",
        "arena_classes_open",
        "arena_classes_manage",
        "arena_ranking_index",
        "arena_help_rating",
        "arena_help_languages",
        "arena_status",
    ):
        app.add_api_route(f"/stub/{route_name}", _stub, methods=["GET"], name=route_name)
    app.add_api_route("/stub/logout", _stub, methods=["POST"], name="arena_logout")

    app.include_router(batch_feedback_router)
    return app


async def _make_user(
    session: AsyncSession, *, role: ArenaRole = ArenaRole.ARENA_USER, prefix: str = "user"
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
        title="Batch Feedback Problem",
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


async def _enroll(session: AsyncSession, arena_class: ArenaClass, user: ArenaUser) -> None:
    session.add(
        ArenaClassMembership(
            class_id=arena_class.id,
            user_id=user.id,
            event_date=date.today() - timedelta(days=5),
            status=ArenaClassMembershipStatus.ACTIVE.value,
        )
    )
    await session.commit()


async def _make_set(session: AsyncSession, arena_class: ArenaClass, *, problem: ArenaProblem) -> ArenaProblemSet:
    problem_set = ArenaProblemSet(class_id=arena_class.id, name=f"Set {uuid.uuid4().hex[:6]}")
    session.add(problem_set)
    await session.flush()
    await session.execute(
        insert(arena_problem_set_problems).values(problem_set_id=problem_set.id, problem_id=problem.id)
    )
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
    compile_log: str | None = None,
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
            compile_log=compile_log,
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


async def _notification_count(session: AsyncSession, *, user_id: str) -> int:
    rows = (
        await session.execute(
            select(arena_notifications.c.id).where(
                arena_notifications.c.user_id == user_id,
                arena_notifications.c.notification_kind == ArenaNotificationKind.TEACHER_FEEDBACK_POSTED.value,
            )
        )
    ).all()
    return len(rows)


@pytest.mark.asyncio
async def test_get_renders_submission_context_before_feedback(session: AsyncSession) -> None:
    """The batch feedback page shows judge context beside the feedback form."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    pset = await _make_set(session, arena_class, problem=problem)
    sub_id = await _make_submission(
        session,
        student,
        problem,
        lang,
        problem_set_id=pset.id,
        compile_log="compiler warning",
    )
    judgment_id = await session.scalar(
        select(arena_submission_judgments.c.id).where(arena_submission_judgments.c.submission_id == sub_id)
    )
    test_case_id = await session.scalar(
        select(arena_test_cases.c.id).where(
            arena_test_cases.c.problem_id == problem.id,
            arena_test_cases.c.ordinal == 1,
        )
    )
    assert judgment_id is not None
    assert test_case_id is not None
    await session.execute(
        insert(arena_submission_test_results).values(
            id=str(uuid.uuid4()),
            judgment_id=judgment_id,
            test_case_id=test_case_id,
            verdict=Verdict.WA.value,
            stdout_excerpt="wrong answer\n",
            stderr_excerpt="stderr note\n",
        )
    )
    await session.execute(
        insert(arena_submission_ai_reviews).values(
            submission_id=sub_id,
            ai_response="The AI suggests checking the boundary case.",
            ai_response_at=datetime(2026, 6, 4, 12, 0, tzinfo=UTC),
            used_platform_key=False,
        )
    )
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, teacher)
        resp = await client.get(
            f"/classes/{arena_class.id}/problem-sets/{pset.id}/problems/{problem.id}/batch-feedback"
        )

    assert resp.status_code == 200
    assert "Compile log" in resp.text
    assert "compiler warning" in resp.text
    assert "Student output" in resp.text
    assert "wrong answer" in resp.text
    assert "Expected output" in resp.text
    assert "stderr note" in resp.text
    assert "AI Review" in resp.text
    assert "The AI suggests checking the boundary case." in resp.text
    assert "Personal API key" in resp.text
    assert f'name="feedback__{sub_id}"' in resp.text


@pytest.mark.asyncio
async def test_post_saves_changed_feedback_notifies_once(session: AsyncSession) -> None:
    """A changed feedback field is saved and generates exactly one notification."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    pset = await _make_set(session, arena_class, problem=problem)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=pset.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, teacher)
        resp = await client.post(
            f"/classes/{arena_class.id}/problem-sets/{pset.id}/problems/{problem.id}/batch-feedback",
            data={f"feedback__{sub_id}": "Check your loop bounds."},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert await get_teacher_feedback_text(session, sub_id) == "Check your loop bounds."
    assert await _notification_count(session, user_id=student.id) == 1


@pytest.mark.asyncio
async def test_post_unchanged_feedback_skipped_no_notification(session: AsyncSession) -> None:
    """Resubmitting the exact same feedback text is a no-op: no upsert bump, no notification."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    arena_class = await _make_class(session, teacher)
    await _enroll(session, arena_class, student)
    problem = await _make_problem(session, teacher)
    pset = await _make_set(session, arena_class, problem=problem)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=pset.id)
    await upsert_teacher_feedback(session, submission_id=sub_id, teacher_id=teacher.id, feedback_text="Existing note")
    await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, teacher)
        resp = await client.post(
            f"/classes/{arena_class.id}/problem-sets/{pset.id}/problems/{problem.id}/batch-feedback",
            data={f"feedback__{sub_id}": "Existing note"},
            follow_redirects=False,
        )

    assert resp.status_code == 303
    assert await get_teacher_feedback_text(session, sub_id) == "Existing note"
    assert await _notification_count(session, user_id=student.id) == 0


@pytest.mark.asyncio
async def test_post_cross_teacher_forbidden(session: AsyncSession) -> None:
    """A teacher who does not manage the URL's own class_id is rejected before ever touching feedback."""
    app = _build_app(session)
    owner_teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="owner")
    other_teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="other")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    arena_class = await _make_class(session, owner_teacher)
    problem = await _make_problem(session, owner_teacher)
    pset = await _make_set(session, arena_class, problem=problem)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=pset.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, other_teacher)
        resp = await client.post(
            f"/classes/{arena_class.id}/problem-sets/{pset.id}/problems/{problem.id}/batch-feedback",
            data={f"feedback__{sub_id}": "I should not be allowed"},
            follow_redirects=False,
        )

    assert resp.status_code == 403
    assert await get_teacher_feedback_text(session, sub_id) is None
    assert await _notification_count(session, user_id=student.id) == 0


@pytest.mark.asyncio
async def test_post_class_set_mismatch_returns_404(session: AsyncSession) -> None:
    """A set that does not belong to the URL's class_id is rejected, even if the actor owns that class."""
    app = _build_app(session)
    teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="teacher")
    other_teacher = await _make_user(session, role=ArenaRole.ARENA_JUDGE, prefix="other")
    student = await _make_user(session, prefix="student")
    lang = await _make_language(session)
    problem = await _make_problem(session, teacher)

    # Teacher owns their own class, but the set/submission belong to another teacher's class.
    own_class = await _make_class(session, teacher)
    other_class = await _make_class(session, other_teacher)
    other_pset = await _make_set(session, other_class, problem=problem)
    sub_id = await _make_submission(session, student, problem, lang, problem_set_id=other_pset.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _login(client, app, teacher)
        resp = await client.post(
            f"/classes/{own_class.id}/problem-sets/{other_pset.id}/problems/{problem.id}/batch-feedback",
            data={f"feedback__{sub_id}": "Sneaky"},
            follow_redirects=False,
        )

    assert resp.status_code == 404
    assert await get_teacher_feedback_text(session, sub_id) is None
    assert await _notification_count(session, user_id=student.id) == 0
