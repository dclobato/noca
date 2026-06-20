#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the Arena submission-heatmap API endpoint."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jwtservice import JWTService, load_token_config_from_dict
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.sessions import SessionMiddleware

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.middleware.auth_middleware import ArenaAuthMiddleware
from arena.models.arena_users import ArenaUser
from arena.routes.user_profile_api import router as user_profile_api_router
from arena.services.token_service import ArenaTokenAction
from shared.db_schema.arena.arena_heatmap import arena_user_submission_heatmap
from shared.enumerations import ArenaRole

TEST_JWT_SECRET = "test-secret-heatmap-api-32bytes-abcd1234"


def _build_api_app(session: AsyncSession) -> FastAPI:
    """Minimal Arena app exposing only the user-profile API router."""
    app = FastAPI()
    app.add_middleware(ArenaAuthMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")
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
    app.include_router(user_profile_api_router)
    return app


async def _create_arena_user(session: AsyncSession) -> ArenaUser:
    """Create and commit an active Arena user."""
    user = ArenaUser(
        nome="Heatmap User",
        email_normalizado=f"heatmap-{uuid.uuid4().hex[:8]}@test.example",
        password_hash="pbkdf2:sha256:1000000$heatmap$testhash",
        role=ArenaRole.ARENA_USER,
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


def _login_token(app: FastAPI, user: ArenaUser) -> str:
    """Issue a valid Arena login token for the supplied user."""
    return str(
        app.state.jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=user.id,
            expires_in=3600,
            extra_data={"tid": user.get_token_id()},
        )
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heatmap_api_unauthenticated_returns_401(session: AsyncSession) -> None:
    """Unauthenticated request must return 401."""
    app = _build_api_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/user/profile/submission-heatmap")
    assert response.status_code == 401
    assert "error" in response.json()


@pytest.mark.asyncio
async def test_heatmap_api_no_row_returns_empty_heatmap(session: AsyncSession) -> None:
    """Authenticated user with no precomputed row gets an empty heatmap with current UTC dates."""
    user = await _create_arena_user(session)
    app = _build_api_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile/submission-heatmap")

    assert response.status_code == 200
    body = response.json()
    assert body["heatmap"] == []
    assert body["computed_at"] is None
    today = datetime.now(UTC).date()
    expected_start = (today - timedelta(days=363)).isoformat()
    expected_end = today.isoformat()
    assert body["range_start"] == expected_start
    assert body["range_end"] == expected_end


@pytest.mark.asyncio
async def test_heatmap_api_existing_row_returned_correctly(session: AsyncSession) -> None:
    """Authenticated user with a stored heatmap receives the correct data."""
    user = await _create_arena_user(session)

    heatmap_data = [["2026-01-10", 3], ["2026-01-15", 7]]
    computed_at = datetime(2026, 6, 12, 10, 0, 0, tzinfo=UTC)
    await session.execute(
        insert(arena_user_submission_heatmap).values(
            user_id=user.id,
            data=heatmap_data,
            range_start="2025-06-13",
            range_end="2026-06-12",
            computed_at=computed_at,
        )
    )
    await session.commit()

    app = _build_api_app(session)
    token = _login_token(app, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.cookies.set("arena_access_token", token)
        response = await client.get("/user/profile/submission-heatmap")

    assert response.status_code == 200
    body = response.json()
    assert body["heatmap"] == heatmap_data
    assert body["range_start"] == "2025-06-13"
    assert body["range_end"] == "2026-06-12"
    assert body["computed_at"] is not None
    assert "2026-06-12" in body["computed_at"]
