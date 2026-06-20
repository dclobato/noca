#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the admin submission list page: service and route layers."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
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
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_users import ArenaUser
from arena.routes.admin_dashboard_history import router
from arena.services import admin_submission_service
from arena.services.admin_user_service import ARENA_ROLE_DISPLAY
from shared.db_schema.arena.arena_submissions import (
    arena_submission_ai_reviews,
    arena_submission_judgments,
    arena_submissions,
)
from shared.enumerations import VERDICT_BADGE_CLASSES, VERDICT_LABELS, ArenaRole
from web.models.language import Language

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _make_user(session: AsyncSession, *, nome: str = "Test User", email: str | None = None) -> ArenaUser:
    """Insert and return a minimal ArenaUser."""
    user = ArenaUser(
        id=str(uuid.uuid4()),
        nome=nome,
        dta_nascimento=date(2000, 1, 1),
        consentimento_responsavel=True,
        role=ArenaRole.ARENA_USER,
        ativo=True,
        email_confirmado=True,
        session_version=0,
        precisa_trocar_senha=False,
        usa_2fa=False,
        com_foto=False,
        ai_backend_credits=0,
        user_rating=0,
        solved_problems=0,
    )
    user.email = email or f"user_{uuid.uuid4().hex[:6]}@test.example"
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


async def _make_language(session: AsyncSession, *, lang_id: str | None = None, name: str = "Test Lang") -> Language:
    """Insert and return a Language."""
    language = Language(
        id=lang_id or f"lang-{uuid.uuid4().hex[:8]}",
        name=name,
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


async def _make_problem(session: AsyncSession, owner_id: str, *, arena_number: int | None = None) -> ArenaProblem:
    """Insert and return an ArenaProblem."""
    problem = ArenaProblem(
        title=f"Test Problem {uuid.uuid4().hex[:6]}",
        owner_id=owner_id,
        problem_statement="<p>Test.</p>",
    )
    if arena_number is not None:
        problem.arena_number = arena_number
    session.add(problem)
    await session.flush()
    return problem


async def _insert_submission(
    session: AsyncSession,
    user_id: str,
    problem_id: str,
    language_id: str,
    *,
    submit_to_ai: bool = False,
    created_at: datetime | None = None,
) -> str:
    """Insert an Arena submission and return its id."""
    sub_id = str(uuid.uuid4())
    values: dict[str, Any] = dict(
        id=sub_id,
        user_id=user_id,
        problem_id=problem_id,
        language_id=language_id,
        source_code="print(1)",
        source_hash="a" * 64,
        source_size_bytes=9,
        submit_to_ai=submit_to_ai,
    )
    if created_at is not None:
        values["created_at"] = created_at
    await session.execute(insert(arena_submissions).values(**values))
    return sub_id


async def _insert_judgment(
    session: AsyncSession,
    submission_id: str,
    *,
    final_verdict: str | None,
    status: str = "DONE",
) -> None:
    """Insert a judgment row for a submission."""
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


async def _insert_ai_review(session: AsyncSession, submission_id: str) -> None:
    """Insert an AI review row for a submission."""
    await session.execute(
        insert(arena_submission_ai_reviews).values(
            submission_id=submission_id,
            ai_response="AI feedback text.",
            ai_response_at=datetime.now(UTC),
            used_platform_key=True,
        )
    )


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_submissions_no_filter_returns_all(session: AsyncSession) -> None:
    """No filters → all inserted submissions are returned."""
    user = await _make_user(session)
    lang = await _make_language(session)
    p1 = await _make_problem(session, user.id)
    p2 = await _make_problem(session, user.id)
    await _insert_submission(session, user.id, p1.id, lang.id)
    await _insert_submission(session, user.id, p2.id, lang.id)
    await session.flush()

    result = await admin_submission_service.list_submissions_paginated(session, page=1, per_page=25)
    assert result.total == 2


@pytest.mark.asyncio
async def test_list_submissions_verdict_filter(session: AsyncSession) -> None:
    """verdict_filter='AC' returns only accepted submissions."""
    user = await _make_user(session)
    lang = await _make_language(session)
    prob = await _make_problem(session, user.id)
    sub_ac = await _insert_submission(session, user.id, prob.id, lang.id)
    sub_wa = await _insert_submission(session, user.id, prob.id, lang.id)
    await _insert_judgment(session, sub_ac, final_verdict="AC")
    await _insert_judgment(session, sub_wa, final_verdict="WA")
    await session.flush()

    result = await admin_submission_service.list_submissions_paginated(
        session, page=1, per_page=25, verdict_filter="AC"
    )
    assert result.total == 1
    assert result.items[0].verdict == "AC"


@pytest.mark.asyncio
async def test_list_submissions_ai_filter_yes(session: AsyncSession) -> None:
    """ai_filter='yes' returns only submissions flagged with submit_to_ai=True."""
    user = await _make_user(session)
    lang = await _make_language(session)
    prob = await _make_problem(session, user.id)
    await _insert_submission(session, user.id, prob.id, lang.id, submit_to_ai=True)
    await _insert_submission(session, user.id, prob.id, lang.id, submit_to_ai=False)
    await session.flush()

    result = await admin_submission_service.list_submissions_paginated(session, page=1, per_page=25, ai_filter="yes")
    assert result.total == 1
    assert result.items[0].submit_to_ai is True


@pytest.mark.asyncio
async def test_list_submissions_ai_filter_no(session: AsyncSession) -> None:
    """ai_filter='no' filters by submit_to_ai=False, not by completed review presence."""
    user = await _make_user(session)
    lang = await _make_language(session)
    prob = await _make_problem(session, user.id)
    sub_ai = await _insert_submission(session, user.id, prob.id, lang.id, submit_to_ai=True)
    await _insert_submission(session, user.id, prob.id, lang.id, submit_to_ai=False)
    await _insert_ai_review(session, sub_ai)
    await session.flush()

    result = await admin_submission_service.list_submissions_paginated(session, page=1, per_page=25, ai_filter="no")
    assert result.total == 1
    assert result.items[0].submit_to_ai is False


@pytest.mark.asyncio
async def test_list_submissions_language_filter(session: AsyncSession) -> None:
    """language_filter restricts results to the given language_id."""
    user = await _make_user(session)
    lang_a = await _make_language(session, lang_id="lang-alpha", name="Alpha")
    lang_b = await _make_language(session, lang_id="lang-beta", name="Beta")
    prob = await _make_problem(session, user.id)
    await _insert_submission(session, user.id, prob.id, lang_a.id)
    await _insert_submission(session, user.id, prob.id, lang_b.id)
    await session.flush()

    result = await admin_submission_service.list_submissions_paginated(
        session, page=1, per_page=25, language_filter="lang-alpha"
    )
    assert result.total == 1
    assert result.items[0].language_id == "lang-alpha"


@pytest.mark.asyncio
async def test_list_submissions_problem_filter_exact(session: AsyncSession) -> None:
    """problem_filter='10' matches only arena_number 10, not 100 or 210."""
    user = await _make_user(session)
    lang = await _make_language(session)
    p10 = await _make_problem(session, user.id, arena_number=10)
    p100 = await _make_problem(session, user.id, arena_number=100)
    p210 = await _make_problem(session, user.id, arena_number=210)
    p2 = await _make_problem(session, user.id, arena_number=2)
    p50 = await _make_problem(session, user.id, arena_number=50)
    for prob in (p10, p100, p210, p2, p50):
        await _insert_submission(session, user.id, prob.id, lang.id)
    await session.flush()

    result = await admin_submission_service.list_submissions_paginated(
        session, page=1, per_page=25, problem_filter="10"
    )
    assert result.total == 1
    assert result.items[0].problem_number == 10


@pytest.mark.asyncio
async def test_list_submissions_sort_dir_orders_by_date(session: AsyncSession) -> None:
    """sort_dir flips the chronological ordering of returned rows."""
    user = await _make_user(session)
    lang = await _make_language(session)
    prob = await _make_problem(session, user.id)
    old = await _insert_submission(session, user.id, prob.id, lang.id, created_at=datetime(2026, 1, 1, tzinfo=UTC))
    new = await _insert_submission(session, user.id, prob.id, lang.id, created_at=datetime(2026, 6, 1, tzinfo=UTC))
    await session.flush()

    desc = await admin_submission_service.list_submissions_paginated(session, page=1, per_page=25, sort_dir="desc")
    assert [row.submission_id for row in desc.items] == [new, old]

    asc = await admin_submission_service.list_submissions_paginated(session, page=1, per_page=25, sort_dir="asc")
    assert [row.submission_id for row in asc.items] == [old, new]


@pytest.mark.asyncio
async def test_list_submissions_date_range_filter(session: AsyncSession) -> None:
    """date_from_utc/date_to_utc restrict results to the submission-date window."""
    user = await _make_user(session)
    lang = await _make_language(session)
    prob = await _make_problem(session, user.id)
    await _insert_submission(session, user.id, prob.id, lang.id, created_at=datetime(2026, 1, 1, tzinfo=UTC))
    inside = await _insert_submission(session, user.id, prob.id, lang.id, created_at=datetime(2026, 3, 15, tzinfo=UTC))
    await _insert_submission(session, user.id, prob.id, lang.id, created_at=datetime(2026, 6, 1, tzinfo=UTC))
    await session.flush()

    result = await admin_submission_service.list_submissions_paginated(
        session,
        page=1,
        per_page=25,
        date_from_utc=datetime(2026, 3, 1, tzinfo=UTC),
        date_to_utc=datetime(2026, 4, 1, tzinfo=UTC),
    )
    assert result.total == 1
    assert result.items[0].submission_id == inside


@pytest.mark.asyncio
async def test_list_submissions_combined_filters(session: AsyncSession) -> None:
    """Multiple filters are ANDed; only matching submissions are returned."""
    user = await _make_user(session)
    lang_a = await _make_language(session, lang_id="combo-alpha", name="ComboAlpha")
    lang_b = await _make_language(session, lang_id="combo-beta", name="ComboBeta")
    prob = await _make_problem(session, user.id)
    sub_match = await _insert_submission(session, user.id, prob.id, lang_a.id, submit_to_ai=True)
    await _insert_submission(session, user.id, prob.id, lang_b.id, submit_to_ai=True)
    await _insert_submission(session, user.id, prob.id, lang_a.id, submit_to_ai=False)
    await _insert_judgment(session, sub_match, final_verdict="WA")
    await session.flush()

    result = await admin_submission_service.list_submissions_paginated(
        session, page=1, per_page=25, ai_filter="yes", language_filter="combo-alpha", verdict_filter="WA"
    )
    assert result.total == 1
    assert result.items[0].submission_id == sub_match


@pytest.mark.asyncio
async def test_list_submissions_search_by_user_name(session: AsyncSession) -> None:
    """Search matches against the submitting user's display name."""
    alice = await _make_user(session, nome="Alice Doe")
    bob = await _make_user(session, nome="Bob Smith")
    lang = await _make_language(session)
    prob = await _make_problem(session, alice.id)
    await _insert_submission(session, alice.id, prob.id, lang.id)
    await _insert_submission(session, bob.id, prob.id, lang.id)
    await session.flush()

    result = await admin_submission_service.list_submissions_paginated(session, page=1, per_page=25, search="alice")
    assert result.total == 1
    assert result.items[0].user_name == "Alice Doe"


@pytest.mark.asyncio
async def test_list_submissions_pending_has_none_verdict(session: AsyncSession) -> None:
    """A submission with a QUEUED judgment has verdict=None and status='QUEUED'."""
    user = await _make_user(session)
    lang = await _make_language(session)
    prob = await _make_problem(session, user.id)
    sub_id = await _insert_submission(session, user.id, prob.id, lang.id)
    await _insert_judgment(session, sub_id, final_verdict=None, status="QUEUED")
    await session.flush()

    result = await admin_submission_service.list_submissions_paginated(session, page=1, per_page=25)
    assert result.total == 1
    row = result.items[0]
    assert row.verdict is None
    assert row.status == "QUEUED"


@pytest.mark.asyncio
async def test_list_submissions_has_ai_review_flag(session: AsyncSession) -> None:
    """has_ai_review is True only when an AI review row exists for the submission."""
    user = await _make_user(session)
    lang = await _make_language(session)
    prob = await _make_problem(session, user.id)
    sub_with = await _insert_submission(session, user.id, prob.id, lang.id, submit_to_ai=True)
    sub_without = await _insert_submission(session, user.id, prob.id, lang.id)
    await _insert_ai_review(session, sub_with)
    await session.flush()

    result = await admin_submission_service.list_submissions_paginated(session, page=1, per_page=25)
    rows_by_id = {row.submission_id: row for row in result.items}
    assert rows_by_id[sub_with].has_ai_review is True
    assert rows_by_id[sub_without].has_ai_review is False


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------


def _build_app(session: Any, *, authorized: bool = True) -> FastAPI:
    """Build a minimal FastAPI app for the admin submissions route tests."""
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
        ("/admin/dashboard/ai-credits", "arena_admin_dashboard_ai_credits"),
    ]:
        app.add_api_route(path, lambda: Response("stub"), name=name)  # type: ignore[arg-type]

    @app.post("/auth/logout", name="arena_logout")
    async def _logout() -> Response:
        return Response("logout")

    @app.get("/admin/users/{user_id}", name="arena_admin_user_profile")
    async def _admin_user_profile(user_id: str) -> Response:
        return Response(f"user {user_id}")

    @app.get("/submissions/{submission_id}", name="arena_submission_detail")
    async def _submission_detail(submission_id: str) -> Response:
        return Response(f"submission {submission_id}")

    @app.get("/user/avatar/{user_id}", name="arena_user_avatar_by_id")
    async def _avatar(user_id: str) -> Response:
        return Response("avatar", media_type="image/svg+xml")

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications() -> Response:
        return Response("[]", media_type="application/json")

    app.include_router(router)

    async def _get_db_override() -> Any:
        yield session

    app.dependency_overrides[get_db] = _get_db_override

    if authorized:

        async def _admin() -> SimpleNamespace:
            return SimpleNamespace(
                id="admin-1",
                email="admin@test.example",
                role=ArenaRole.ARENA_ADMIN,
                nome="Admin",
                dta_foto=None,
                timezone="UTC",
            )

        app.dependency_overrides[require_arena_admin] = _admin
    else:

        async def _reject() -> None:
            raise HTTPException(status_code=403)

        app.dependency_overrides[require_arena_admin] = _reject

    return app


@pytest.mark.asyncio
async def test_submissions_route_returns_200_for_admin(session: AsyncSession) -> None:
    """GET /admin/dashboard/submissions returns 200 for an authorized admin."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/submissions")
    assert response.status_code == 200
    assert "Submissions" in response.text


@pytest.mark.asyncio
async def test_submissions_route_returns_403_for_non_admin(session: AsyncSession) -> None:
    """GET /admin/dashboard/submissions returns 403 for an unauthorized user."""
    app = _build_app(session, authorized=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/submissions")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_submissions_route_invalid_per_page_falls_back_to_default(session: AsyncSession) -> None:
    """An invalid per_page value falls back to the default and the page loads."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/submissions", params={"per_page": "9999"})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_submissions_route_combined_filters_return_200(session: AsyncSession) -> None:
    """Combining all filter params at once still returns 200 (no crash on invalid filter values)."""
    app = _build_app(session)
    params = {
        "search": "Alice",
        "verdict_filter": "AC",
        "ai_filter": "yes",
        "language_filter": "python3",
        "problem_filter": "10",
        "per_page": "25",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/submissions", params=params)
    assert response.status_code == 200
