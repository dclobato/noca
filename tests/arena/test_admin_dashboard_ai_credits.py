#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the admin AI Credits Usage page: service and route layers."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

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

import arena.models.arena_ai_credit_transactions  # noqa: F401 — register ORM mapper
import arena.models.arena_problems  # noqa: F401 — register ORM mapper
import arena.models.arena_submissions  # noqa: F401 — register ORM mapper
import arena.models.arena_users  # noqa: F401 — register ORM mapper
from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_users import ArenaUser
from arena.routes.admin_dashboard import router
from arena.services import admin_ai_credits_service
from shared.db_schema.arena import arena_ai_batch_jobs
from shared.db_schema.arena.arena_ai_credit_transactions import arena_ai_credit_transactions
from shared.db_schema.arena.arena_submissions import arena_submission_ai_reviews, arena_submissions
from shared.enumerations import ArenaRole
from shared.queue_schema import AIBatchTurnaroundStats
from shared.timing import format_compact_duration

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _make_user(
    session: AsyncSession,
    *,
    nome: str = "Test User",
    email: str | None = None,
    credits: int = 5,
) -> ArenaUser:
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
        ai_backend_credits=credits,
        user_rating=0,
        solved_problems=0,
    )
    user.email = email or f"user_{uuid.uuid4().hex[:6]}@test.example"
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


async def _insert_consumption(
    session: AsyncSession,
    user_id: str,
    *,
    submission_id: str | None = None,
    created_at: datetime | None = None,
) -> str:
    """Insert a consumption credit transaction and return its id."""
    tx_id = str(uuid.uuid4())
    await session.execute(
        insert(arena_ai_credit_transactions).values(
            id=tx_id,
            user_id=user_id,
            amount=-1,
            balance_after=0,
            transaction_type="consumption",
            submission_id=submission_id,
            created_at=created_at or datetime.now(UTC),
        )
    )
    return tx_id


async def _insert_topup(session: AsyncSession, user_id: str) -> str:
    """Insert a topup credit transaction and return its id."""
    tx_id = str(uuid.uuid4())
    await session.execute(
        insert(arena_ai_credit_transactions).values(
            id=tx_id,
            user_id=user_id,
            amount=10,
            balance_after=10,
            transaction_type="topup",
            created_at=datetime.now(UTC),
        )
    )
    return tx_id


async def _insert_submission(session: AsyncSession, user_id: str) -> str:
    """Insert a minimal arena submission and return its id.

    SQLite does not enforce FK constraints by default, so placeholder UUIDs
    are used for problem_id and language_id to avoid setting up full fixtures.
    """
    sub_id = str(uuid.uuid4())
    await session.execute(
        insert(arena_submissions).values(
            id=sub_id,
            user_id=user_id,
            problem_id=str(uuid.uuid4()),
            language_id=str(uuid.uuid4()),
            source_code="print(1)",
            source_hash="a" * 64,
            source_size_bytes=9,
            submit_to_ai=False,
        )
    )
    return sub_id


async def _insert_ai_review(
    session: AsyncSession,
    submission_id: str,
    *,
    cost_micros: int | None = None,
    response_at: datetime | None = None,
) -> None:
    """Insert an arena_submission_ai_reviews row."""
    await session.execute(
        insert(arena_submission_ai_reviews).values(
            submission_id=submission_id,
            ai_response="Review text.",
            ai_response_at=response_at or datetime.now(UTC),
            _ai_review_cost=cost_micros,
            used_platform_key=True,
        )
    )


async def _insert_batch_job(
    session: AsyncSession,
    submission_id: str,
    *,
    created_at: datetime,
) -> None:
    """Insert a completed batch job for turnaround tests."""
    await session.execute(
        insert(arena_ai_batch_jobs).values(
            id=str(uuid.uuid4()),
            submission_id=submission_id,
            local_status="completed",
            created_at=created_at,
            completed_at=created_at,
        )
    )


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_returns_only_consumption_rows(session: AsyncSession) -> None:
    """list_consumption_transactions_paginated excludes topup transactions."""
    user = await _make_user(session)
    await _insert_consumption(session, user.id)
    await _insert_topup(session, user.id)
    await session.flush()

    result = await admin_ai_credits_service.list_consumption_transactions_paginated(session, page=1, per_page=25)

    assert result.total == 1
    assert all(tx.transaction_type == "consumption" for tx in result.items)


@pytest.mark.asyncio
async def test_service_returns_batch_turnaround_seconds(session: AsyncSession) -> None:
    """Batch turnaround uses staging and review-storage timestamps."""
    user = await _make_user(session)
    sub_id = await _insert_submission(session, user.id)
    response_at = datetime.now(UTC)
    await _insert_batch_job(
        session,
        sub_id,
        created_at=response_at - timedelta(seconds=42, milliseconds=900),
    )
    await _insert_ai_review(session, sub_id, response_at=response_at)
    await session.flush()

    result = await admin_ai_credits_service.get_batch_turnaround_seconds(session, {sub_id})

    assert result == {sub_id: 42}


@pytest.mark.asyncio
async def test_service_search_by_name(session: AsyncSession) -> None:
    """Search filters by user full name (case-insensitive)."""
    alice = await _make_user(session, nome="Alice Wonderland")
    bob = await _make_user(session, nome="Bob Builder")
    await _insert_consumption(session, alice.id)
    await _insert_consumption(session, bob.id)
    await session.flush()

    result = await admin_ai_credits_service.list_consumption_transactions_paginated(
        session, page=1, per_page=25, search="alice"
    )

    assert result.total == 1
    assert result.items[0].user_id == alice.id


@pytest.mark.asyncio
async def test_service_search_miss(session: AsyncSession) -> None:
    """Search returns zero results when no user matches."""
    user = await _make_user(session, nome="Only Person")
    await _insert_consumption(session, user.id)
    await session.flush()

    result = await admin_ai_credits_service.list_consumption_transactions_paginated(
        session, page=1, per_page=25, search="nobody"
    )

    assert result.total == 0


@pytest.mark.asyncio
async def test_service_search_by_email(session: AsyncSession) -> None:
    """Search filters by normalised email (case-insensitive)."""
    user = await _make_user(session, email="searchme@test.example")
    await _insert_consumption(session, user.id)
    await session.flush()

    result = await admin_ai_credits_service.list_consumption_transactions_paginated(
        session, page=1, per_page=25, search="SEARCHME"
    )

    assert result.total == 1
    assert result.items[0].user_id == user.id


@pytest.mark.asyncio
async def test_service_sort_asc(session: AsyncSession) -> None:
    """sort_dir='asc' returns the oldest transaction first."""
    user = await _make_user(session)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    await _insert_consumption(session, user.id, created_at=base)
    await _insert_consumption(session, user.id, created_at=base + timedelta(hours=1))
    await session.flush()

    result = await admin_ai_credits_service.list_consumption_transactions_paginated(
        session, page=1, per_page=25, sort_dir="asc"
    )

    assert result.items[0].created_at <= result.items[1].created_at


@pytest.mark.asyncio
async def test_service_sort_desc(session: AsyncSession) -> None:
    """sort_dir='desc' (default) returns the newest transaction first."""
    user = await _make_user(session)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    await _insert_consumption(session, user.id, created_at=base)
    await _insert_consumption(session, user.id, created_at=base + timedelta(hours=1))
    await session.flush()

    result = await admin_ai_credits_service.list_consumption_transactions_paginated(
        session, page=1, per_page=25, sort_dir="desc"
    )

    assert result.items[0].created_at >= result.items[1].created_at


@pytest.mark.asyncio
async def test_service_page_clamping(session: AsyncSession) -> None:
    """Out-of-range page numbers are clamped to the last available page."""
    user = await _make_user(session)
    for _ in range(5):
        await _insert_consumption(session, user.id)
    await session.flush()

    result = await admin_ai_credits_service.list_consumption_transactions_paginated(session, page=999, per_page=3)

    assert result.page == 2
    assert len(result.items) == 2


@pytest.mark.asyncio
async def test_service_pending_cost_when_no_ai_review(session: AsyncSession) -> None:
    """Submission without an ai_review row gives None for ai_review on the transaction."""
    user = await _make_user(session)
    sub_id = await _insert_submission(session, user.id)
    await _insert_consumption(session, user.id, submission_id=sub_id)
    await session.flush()

    result = await admin_ai_credits_service.list_consumption_transactions_paginated(session, page=1, per_page=25)

    tx = result.items[0]
    assert tx.submission is not None
    assert tx.submission.ai_review is None


@pytest.mark.asyncio
async def test_service_pending_cost_when_ai_review_cost_is_null(session: AsyncSession) -> None:
    """An existing ai_review row with no cost stored gives ai_review_cost=None."""
    user = await _make_user(session)
    sub_id = await _insert_submission(session, user.id)
    await _insert_ai_review(session, sub_id, cost_micros=None)
    await _insert_consumption(session, user.id, submission_id=sub_id)
    await session.flush()

    result = await admin_ai_credits_service.list_consumption_transactions_paginated(session, page=1, per_page=25)

    tx = result.items[0]
    assert tx.submission is not None
    assert tx.submission.ai_review is not None
    assert tx.submission.ai_review.ai_review_cost is None


@pytest.mark.asyncio
async def test_service_cost_value_when_ai_review_exists(session: AsyncSession) -> None:
    """A stored cost of 1234 micros is returned as 0.001234 by the property."""
    user = await _make_user(session)
    sub_id = await _insert_submission(session, user.id)
    await _insert_ai_review(session, sub_id, cost_micros=1234)
    await _insert_consumption(session, user.id, submission_id=sub_id)
    await session.flush()

    result = await admin_ai_credits_service.list_consumption_transactions_paginated(session, page=1, per_page=25)

    tx = result.items[0]
    assert tx.submission is not None
    assert tx.submission.ai_review is not None
    assert abs((tx.submission.ai_review.ai_review_cost or 0) - 0.001234) < 1e-9


@pytest.mark.asyncio
async def test_service_date_from_utc_filters_earlier_records(session: AsyncSession) -> None:
    """date_from_utc excludes transactions before the given UTC datetime."""
    user = await _make_user(session)
    base = datetime(2026, 3, 10, tzinfo=UTC)
    await _insert_consumption(session, user.id, created_at=base - timedelta(days=1))
    await _insert_consumption(session, user.id, created_at=base)
    await session.flush()

    result = await admin_ai_credits_service.list_consumption_transactions_paginated(
        session, page=1, per_page=25, date_from_utc=datetime(2026, 3, 10, tzinfo=UTC)
    )

    assert result.total == 1
    assert result.items[0].created_at.date() == date(2026, 3, 10)


@pytest.mark.asyncio
async def test_service_date_to_utc_filters_later_records(session: AsyncSession) -> None:
    """date_to_utc is an exclusive upper bound; records on and after it are excluded."""
    user = await _make_user(session)
    base = datetime(2026, 3, 10, 23, 59, tzinfo=UTC)
    await _insert_consumption(session, user.id, created_at=base)
    await _insert_consumption(session, user.id, created_at=base + timedelta(hours=2))
    await session.flush()

    result = await admin_ai_credits_service.list_consumption_transactions_paginated(
        session, page=1, per_page=25, date_to_utc=datetime(2026, 3, 11, tzinfo=UTC)
    )

    assert result.total == 1


@pytest.mark.asyncio
async def test_service_date_range_utc_combined(session: AsyncSession) -> None:
    """date_from_utc + date_to_utc together restrict results to the given window."""
    user = await _make_user(session)
    dates = [date(2026, 3, d) for d in (8, 9, 10, 11, 12)]
    for d in dates:
        await _insert_consumption(session, user.id, created_at=datetime(d.year, d.month, d.day, tzinfo=UTC))
    await session.flush()

    result = await admin_ai_credits_service.list_consumption_transactions_paginated(
        session,
        page=1,
        per_page=25,
        date_from_utc=datetime(2026, 3, 9, tzinfo=UTC),
        date_to_utc=datetime(2026, 3, 12, tzinfo=UTC),
    )

    assert result.total == 3


# ---------------------------------------------------------------------------
# Route integration tests
# ---------------------------------------------------------------------------


def _build_app(
    session: Any,
    *,
    authorized: bool = True,
    turnaround_stats: AIBatchTurnaroundStats | None = None,
) -> FastAPI:
    """Build a minimal FastAPI app for dashboard AI credits route tests."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session-secret")

    arena_dir = Path(__file__).resolve().parents[2] / "arena"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=arena_dir / "template")
    templates.env.globals["arena_format_datetime"] = lambda value, user, fmt=None: value.isoformat()
    templates.env.globals["arena_format_relative_datetime"] = lambda value: "just now"
    templates.env.globals["format_compact_duration"] = format_compact_duration
    templates.env.globals["app_version"] = "test"
    templates.env.globals["next_rating_update_text"] = lambda request: None
    setup_flash(templates)
    app.state.arena_templates = templates
    app.state.valkey_runtime = SimpleNamespace(
        get=AsyncMock(return_value=turnaround_stats.model_dump_json() if turnaround_stats else None)
    )

    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/shared-js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/css", StaticFiles(directory=arena_dir / "static" / "css"), name="arena_static_css")
    app.mount("/static/js", StaticFiles(directory=arena_dir / "static" / "js"), name="arena_static_js")
    app.mount("/static/img", StaticFiles(directory=arena_dir / "static" / "img"), name="arena_static_img")

    # Stub routes required by _base.html navigation
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

    @app.get("/user/profile", name="arena_user_profile")
    async def _user_profile() -> Response:
        return Response("profile")

    @app.post("/auth/logout", name="arena_logout")
    async def _logout() -> Response:
        return Response("logout")

    @app.get("/problems", name="arena_problem_list")
    async def _problems() -> Response:
        return Response("problems")

    @app.get("/classes", name="arena_classes_index")
    async def _classes() -> Response:
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

    @app.get("/ranking/users", name="arena_ranking_users")
    async def _ranking_users() -> Response:
        return Response("ranking users")

    @app.get("/ranking/affiliations", name="arena_ranking_affiliations")
    async def _ranking_affiliations() -> Response:
        return Response("ranking affiliations")

    @app.get("/help/rating", name="arena_help_rating")
    async def _help_rating() -> Response:
        return Response("help rating")

    @app.get("/help/languages", name="arena_help_languages")
    async def _help_languages() -> Response:
        return Response("help languages")

    @app.get("/admin/problems", name="arena_admin_problem_list")
    async def _admin_problems() -> Response:
        return Response("admin problems")

    @app.get("/admin/users", name="arena_admin_user_list")
    async def _admin_users() -> Response:
        return Response("admin users")

    @app.get("/admin/users/{user_id}", name="arena_admin_user_profile")
    async def _admin_user_profile(user_id: str) -> Response:
        return Response(f"admin user {user_id}")

    @app.get("/admin/categories", name="arena_admin_category_list")
    async def _admin_categories() -> Response:
        return Response("admin categories")

    @app.get("/admin/affiliations", name="arena_admin_affiliation_list")
    async def _admin_affiliations() -> Response:
        return Response("admin affiliations")

    @app.get("/submissions/{submission_id}", name="arena_submission_detail")
    async def _submission_detail(submission_id: str) -> Response:
        return Response(f"submission {submission_id}")

    @app.get("/user/avatar/{user_id}", name="arena_user_avatar_by_id")
    async def _avatar(user_id: str) -> Response:
        return Response("avatar", media_type="image/svg+xml")

    @app.get("/arena/notifications", name="arena_notifications_list")
    async def _notifications() -> Response:
        return Response("[]", media_type="application/json")

    @app.get("/admin/dashboard/login-history", name="arena_admin_dashboard_login_history")
    async def _dashboard_login_history() -> Response:
        return Response("login history")

    @app.get("/admin/dashboard/submissions", name="arena_admin_dashboard_submissions")
    async def _dashboard_submissions() -> Response:
        return Response("submissions")

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
                can_edit=True,
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
async def test_ai_credits_route_returns_200_for_admin(session: AsyncSession) -> None:
    """GET /admin/dashboard/ai-credits returns 200 for an authorized admin."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/ai-credits")
    assert response.status_code == 200
    assert "AI Credits Usage" in response.text


@pytest.mark.asyncio
async def test_ai_credits_route_returns_403_for_non_admin(session: AsyncSession) -> None:
    """GET /admin/dashboard/ai-credits returns 403 for an unauthorized user."""
    app = _build_app(session, authorized=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/ai-credits")
    assert response.status_code == 403


@pytest.mark.parametrize("size", [10, 25, 50, 100, 500])
@pytest.mark.asyncio
async def test_ai_credits_route_accepts_valid_per_page_sizes(session: AsyncSession, size: int) -> None:
    """All allowed per-page values (10/25/50/100/500) return 200."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/ai-credits", params={"per_page": str(size)})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ai_credits_route_invalid_per_page_falls_back_to_default(
    session: AsyncSession,
) -> None:
    """An invalid per_page value falls back to 25 and returns 200."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/ai-credits", params={"per_page": "999"})
    assert response.status_code == 200
    assert 'value="25" selected' in response.text


@pytest.mark.asyncio
async def test_ai_credits_route_invalid_sort_dir_normalized_to_desc(
    session: AsyncSession,
) -> None:
    """Invalid sort_dir is silently normalized to 'desc'."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/ai-credits", params={"sort_dir": "invalid"})
    assert response.status_code == 200
    assert 'value="desc" selected' in response.text


@pytest.mark.asyncio
async def test_ai_credits_route_renders_user_link(session: AsyncSession) -> None:
    """The user column links to arena_admin_user_profile."""
    user = await _make_user(session, nome="Link Test User")
    await _insert_consumption(session, user.id)
    await session.flush()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/ai-credits")

    assert f"/admin/users/{user.id}" in response.text
    assert "Link Test User" in response.text


@pytest.mark.asyncio
async def test_ai_credits_route_renders_submission_link(session: AsyncSession) -> None:
    """The submission column links to arena_submission_detail when submission_id is set."""
    user = await _make_user(session)
    sub_id = await _insert_submission(session, user.id)
    await _insert_consumption(session, user.id, submission_id=sub_id)
    await session.flush()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/ai-credits")

    assert f"/submissions/{sub_id}" in response.text
    assert "Submission" in response.text


@pytest.mark.asyncio
async def test_ai_credits_route_renders_cost_with_six_decimals(session: AsyncSession) -> None:
    """Cost column shows $X.XXXXXX when ai_review_cost is present."""
    user = await _make_user(session)
    sub_id = await _insert_submission(session, user.id)
    await _insert_ai_review(session, sub_id, cost_micros=1_234_567)
    await _insert_consumption(session, user.id, submission_id=sub_id)
    await session.flush()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/ai-credits")

    assert "$1.234567" in response.text


@pytest.mark.asyncio
async def test_ai_credits_route_renders_batch_turnaround_column(session: AsyncSession) -> None:
    """Turnaround column shows a compact elapsed duration for a completed batch review."""
    user = await _make_user(session)
    sub_id = await _insert_submission(session, user.id)
    response_at = datetime.now(UTC)
    await _insert_batch_job(
        session,
        sub_id,
        created_at=response_at - timedelta(seconds=73, milliseconds=500),
    )
    await _insert_ai_review(session, sub_id, cost_micros=1_234, response_at=response_at)
    await _insert_consumption(session, user.id, submission_id=sub_id)
    await session.flush()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/ai-credits")

    assert response.status_code == 200
    assert '<th class="text-end">Turnaround</th>' in response.text
    assert "1m13s" in response.text


@pytest.mark.asyncio
async def test_ai_credits_route_renders_turnaround_statistics(session: AsyncSession) -> None:
    """Recent batch turnaround statistics appear above the filter bar."""
    stats = AIBatchTurnaroundStats(
        average_seconds=248.4,
        median_seconds=180,
        stddev_seconds=31.2,
        sample_count=42,
        updated_at=datetime.now(UTC),
    )
    app = _build_app(session, turnaround_stats=stats)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/ai-credits")

    assert response.status_code == 200
    assert "Average turn around for last 42 reviews:" in response.text
    assert "4m08s" in response.text
    assert "std dev 31s" in response.text
    assert "Median turnaround for last 42 reviews:" in response.text
    assert "3m00s" in response.text


@pytest.mark.asyncio
async def test_ai_credits_route_handles_unavailable_turnaround_statistics(session: AsyncSession) -> None:
    """The dashboard explains when batch turnaround statistics are absent."""
    app = _build_app(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/ai-credits")

    assert response.status_code == 200
    assert "Turnaround statistics are not available." in response.text


@pytest.mark.asyncio
async def test_ai_credits_route_renders_pending_when_no_review_cost(
    session: AsyncSession,
) -> None:
    """Cost column shows 'Pending' when ai_review_cost is absent."""
    user = await _make_user(session)
    sub_id = await _insert_submission(session, user.id)
    await _insert_ai_review(session, sub_id, cost_micros=None)
    await _insert_consumption(session, user.id, submission_id=sub_id)
    await session.flush()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/ai-credits")

    assert "Pending" in response.text


@pytest.mark.asyncio
async def test_ai_credits_route_blank_date_strings_return_200(session: AsyncSession) -> None:
    """Submitting the filter form with blank date fields must not trigger HTTP 422."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/admin/dashboard/ai-credits",
            params={"date_from": "", "date_to": "", "search": "x"},
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ai_credits_route_nav_blocks_active(session: AsyncSession) -> None:
    """The AI Credits page activates nav_admin_dashboard and nav_admin_dashboard_ai_credits."""
    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/dashboard/ai-credits")

    assert response.status_code == 200
    assert "AI Credits Usage" in response.text
    assert "Service Status" in response.text
