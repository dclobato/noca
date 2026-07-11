#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for admin-role access to the Arena submission detail page.

Verifies that ARENA_ADMIN users may view any submission and that AI review
and teacher feedback content are rendered when the corresponding data exists.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_problems  # noqa: F401 — register ORM mapper
import arena.models.arena_submissions  # noqa: F401 — register ORM mapper
import arena.models.arena_users  # noqa: F401 — register ORM mapper
from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.routes.submissions import router as submission_router
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from shared.db_schema.arena.arena_submissions import (
    arena_submission_ai_reviews,
    arena_submission_judgments,
    arena_submission_teacher_feedback,
    arena_submissions,
)
from shared.enumerations import VERDICT_BADGE_CLASSES, VERDICT_LABELS, ArenaRole
from web.models.language import Language

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _make_user(
    session: AsyncSession,
    *,
    nome: str = "Test User",
    role: ArenaRole = ArenaRole.ARENA_USER,
) -> ArenaUser:
    """Insert and return a minimal ArenaUser."""
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome=nome,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        role=role,
        ativo=True,
        email_confirmado=True,
        session_version=0,
        precisa_trocar_senha=False,
        usa_2fa=False,
        com_foto=False,
        ai_backend_credits=5,
        user_rating=0,
        solved_problems=0,
    )
    user.email = f"user_{uuid.uuid4().hex[:6]}@test.example"
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


async def _make_language(session: AsyncSession) -> Language:
    """Insert and return a Language."""
    language = Language(
        id=f"detail-lang-{uuid.uuid4().hex[:8]}",
        name="Detail Test Lang",
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


async def _make_problem(session: AsyncSession, owner_id: str) -> ArenaProblem:
    """Insert and return an ArenaProblem."""
    problem = ArenaProblem(
        title=f"Detail Test Problem {uuid.uuid4().hex[:6]}",
        owner_id=owner_id,
        problem_statement="<p>Test.</p>",
    )
    session.add(problem)
    await session.flush()
    return problem


async def _insert_submission(session: AsyncSession, user_id: str, problem_id: str, language_id: str) -> str:
    """Insert a minimal Arena submission and return its id."""
    sub_id = str(uuid.uuid4())
    await session.execute(
        insert(arena_submissions).values(
            id=sub_id,
            user_id=user_id,
            problem_id=problem_id,
            language_id=language_id,
            source_code="print(42)",
            source_hash="b" * 64,
            source_size_bytes=10,
            submit_to_ai=False,
        )
    )
    return sub_id


async def _insert_judgment(
    session: AsyncSession, submission_id: str, *, final_verdict: str | None, status: str = "DONE"
) -> None:
    """Insert a judgment row (non-superseded) for the given submission."""
    await session.execute(
        insert(arena_submission_judgments).values(
            id=str(uuid.uuid4()),
            submission_id=submission_id,
            status=status,
            final_verdict=final_verdict,
            autojudge_verdict=final_verdict,
            created_at=datetime.now(UTC),
        )
    )


async def _insert_ai_review(session: AsyncSession, submission_id: str, *, text: str = "AI feedback.") -> None:
    """Insert an AI review row for the given submission."""
    await session.execute(
        insert(arena_submission_ai_reviews).values(
            submission_id=submission_id,
            ai_response=text,
            ai_response_at=datetime.now(UTC),
            used_platform_key=True,
        )
    )


async def _insert_teacher_feedback(
    session: AsyncSession, submission_id: str, *, text: str = "Teacher comment."
) -> None:
    """Insert a teacher feedback row for the given submission."""
    await session.execute(
        insert(arena_submission_teacher_feedback).values(
            submission_id=submission_id,
            feedback_text=text,
            feedback_at=datetime.now(UTC),
        )
    )


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------


def _build_app(session: AsyncSession, admin_user: ArenaUser) -> FastAPI:
    """Build a minimal FastAPI app for the submission detail admin view tests."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret")

    arena_dir = Path(__file__).resolve().parents[2] / "arena"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=arena_dir / "template")
    templates.env.globals["arena_format_datetime"] = lambda value, user, fmt=None: str(value)
    templates.env.globals["arena_format_relative_datetime"] = lambda value: "just now"
    templates.env.globals["app_version"] = "test"
    templates.env.globals["next_rating_update_text"] = lambda request: None
    templates.env.globals["verdict_badge_classes"] = VERDICT_BADGE_CLASSES
    templates.env.globals["verdict_labels"] = VERDICT_LABELS
    templates.env.globals["arena_role_labels"] = ARENA_ROLE_DISPLAY
    setup_flash(templates)
    app.state.arena_templates = templates

    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")

    for path, name in [
        ("/", "arena_dashboard"),
        ("/status", "arena_status"),
        ("/auth/login", "arena_login"),
        ("/auth/signup", "arena_signup"),
        ("/user/profile", "arena_user_profile"),
        ("/problems", "arena_problem_list"),
        ("/classes", "arena_classes_index"),
        ("/classes/registered", "arena_classes_registered"),
        ("/classes/open", "arena_classes_open"),
        ("/classes/manage", "arena_classes_manage"),
        ("/ranking", "arena_ranking_index"),
        ("/ranking/users", "arena_ranking_users"),
        ("/ranking/affiliations", "arena_ranking_affiliations"),
        ("/help/rating", "arena_help_rating"),
        ("/help/languages", "arena_help_languages"),
        ("/admin/problems", "arena_admin_problem_list"),
        ("/admin/users", "arena_admin_user_list"),
        ("/admin/categories", "arena_admin_category_list"),
        ("/admin/affiliations", "arena_admin_affiliation_list"),
        ("/admin/dashboard", "arena_admin_dashboard"),
        ("/admin/dashboard/service-status", "arena_admin_dashboard_service_status"),
        ("/admin/dashboard/ai-usage", "arena_admin_dashboard_ai_usage"),
        ("/admin/dashboard/login-history", "arena_admin_dashboard_login_history"),
        ("/admin/dashboard/submissions", "arena_admin_dashboard_submissions"),
        ("/admin/dashboard/security-events", "arena_admin_dashboard_security_events"),
    ]:
        app.add_api_route(path, lambda: Response("stub"), name=name)  # type: ignore[arg-type]

    @app.post("/auth/logout", name="arena_logout")
    async def _logout() -> Response:
        return Response("logout")

    @app.get("/admin/users/{user_id}", name="arena_admin_user_profile")
    async def _admin_user_profile(user_id: str) -> Response:
        return Response(f"user {user_id}")

    @app.get("/problems/{arena_number}", name="arena_problem_detail")
    async def _problem_detail(arena_number: int) -> Response:
        return Response(f"problem {arena_number}")

    @app.get("/user/avatar/{user_id}", name="arena_user_avatar_by_id")
    async def _avatar(user_id: str) -> Response:
        return Response("avatar", media_type="image/svg+xml")

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications() -> Response:
        return Response("[]", media_type="application/json")

    app.include_router(submission_router)

    async def _get_db_override() -> Any:
        yield session

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_current_arena_user] = lambda: admin_user

    return app


@pytest.mark.asyncio
async def test_admin_can_view_another_users_submission(session: AsyncSession) -> None:
    """An ARENA_ADMIN user gets 200 when viewing a submission they do not own."""
    admin = await _make_user(session, nome="Admin", role=ArenaRole.ARENA_ADMIN)
    owner = await _make_user(session, nome="Owner User")
    lang = await _make_language(session)
    prob = await _make_problem(session, owner.id)
    sub_id = await _insert_submission(session, owner.id, prob.id, lang.id)
    await session.flush()

    app = _build_app(session, admin)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/submissions/{sub_id}")

    assert response.status_code == 200
    assert "Owner User" in response.text
    assert f"/admin/users/{owner.id}" in response.text


@pytest.mark.asyncio
async def test_non_owner_non_admin_gets_404(session: AsyncSession) -> None:
    """A non-admin user who does not own the submission receives 404."""
    owner = await _make_user(session, nome="Owner User")
    other = await _make_user(session, nome="Other User")
    lang = await _make_language(session)
    prob = await _make_problem(session, owner.id)
    sub_id = await _insert_submission(session, owner.id, prob.id, lang.id)
    await session.flush()

    app = _build_app(session, other)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/submissions/{sub_id}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_sees_ai_review_content(session: AsyncSession) -> None:
    """AI review text is rendered in the response when a review row exists."""
    admin = await _make_user(session, nome="Admin", role=ArenaRole.ARENA_ADMIN)
    owner = await _make_user(session, nome="Submitter")
    lang = await _make_language(session)
    prob = await _make_problem(session, owner.id)
    sub_id = await _insert_submission(session, owner.id, prob.id, lang.id)
    await _insert_judgment(session, sub_id, final_verdict="WA")
    await _insert_ai_review(session, sub_id, text="You should use a dictionary here.")
    await session.flush()

    app = _build_app(session, admin)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/submissions/{sub_id}")

    assert response.status_code == 200
    assert "You should use a dictionary here." in response.text


@pytest.mark.asyncio
async def test_admin_sees_teacher_feedback_content(session: AsyncSession) -> None:
    """Teacher feedback text is rendered in the response for a non-AC submission with feedback."""
    admin = await _make_user(session, nome="Admin", role=ArenaRole.ARENA_ADMIN)
    owner = await _make_user(session, nome="Student")
    lang = await _make_language(session)
    prob = await _make_problem(session, owner.id)
    sub_id = await _insert_submission(session, owner.id, prob.id, lang.id)
    await _insert_judgment(session, sub_id, final_verdict="WA")
    await _insert_teacher_feedback(session, sub_id, text="Check your boundary conditions.")
    await session.flush()

    app = _build_app(session, admin)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/submissions/{sub_id}")

    assert response.status_code == 200
    assert "Check your boundary conditions." in response.text
