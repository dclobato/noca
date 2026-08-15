#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for the Arena contestant-facing public problem export."""

from __future__ import annotations

import io
import tempfile
import uuid
import zipfile
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.config import settings as arena_settings
from arena.database import get_db
from arena.dependencies.auth import require_arena_user
from arena.models.arena_problems import ArenaTestCase
from arena.models.arena_users import ArenaUser
from arena.routes import problems as problems_routes
from arena.services import admin_problem_service
from shared.enumerations import ArenaRole, ProblemValidatorType
from shared.services.problem_package.staging import STAGING_PREFIX
from shared.services.testcase_files import save_testcase_files


async def _create_user(session: AsyncSession) -> ArenaUser:
    """Create a plain Arena user, the audience for the public export."""
    user = ArenaUser(
        nome="Contestant",
        email_normalizado="contestant@test.example",
        password_hash="hash",
        role=ArenaRole.ARENA_USER,
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


def _build_app(session: AsyncSession, current_user: ArenaUser) -> FastAPI:
    """Build a minimal app carrying the public problem router."""
    app = FastAPI()

    async def _override_db() -> AsyncGenerator[AsyncSession]:
        yield session

    async def _override_current_user() -> ArenaUser:
        return current_user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_arena_user] = _override_current_user
    app.include_router(problems_routes.router)
    return app


async def _make_problem(session: AsyncSession, owner: ArenaUser, *, enabled: bool) -> object:
    """Create a problem with one public and one secret test case."""
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=owner.id,
        title="Public Export",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="# Statement\n\nNo external links here.\n",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes="internal only",
        category_ids=[],
        validator_type=ProblemValidatorType.STANDARD,
    )
    for ordinal, is_sample in ((1, True), (2, False)):
        session.add(
            ArenaTestCase(
                id=str(uuid.uuid4()),
                problem_id=problem.id,
                ordinal=ordinal,
                is_sample=is_sample,
                input_size_bytes=2,
                output_size_bytes=2,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        save_testcase_files(
            problem.id,
            ordinal,
            f"{ordinal}\n".encode(),
            f"{ordinal}\n".encode(),
            arena_settings.PROBLEM_TESTCASE_DIR,
        )
    problem.enabled = enabled
    await session.commit()
    return problem


@pytest.mark.asyncio
async def test_public_export_ships_only_the_contestant_bundle(session: AsyncSession) -> None:
    user = await _create_user(session)
    problem = await _make_problem(session, user, enabled=True)

    app = _build_app(session, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/problems/{problem.arena_number}/export")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    names = set(zipfile.ZipFile(io.BytesIO(response.content)).namelist())
    # Statement and the public case only: no problem.json, no secret case, and
    # therefore nothing that would let it be re-imported.
    assert "statement.md" in names
    assert {"in/001.in", "out/001.out"} <= names
    assert "problem.json" not in names
    assert not any(name.startswith(("in/002", "out/002")) for name in names)


@pytest.mark.asyncio
async def test_public_export_404s_for_a_disabled_problem(session: AsyncSession) -> None:
    """Visibility is gated exactly as the detail page is."""
    user = await _create_user(session)
    problem = await _make_problem(session, user, enabled=False)

    app = _build_app(session, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/problems/{problem.arena_number}/export")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_public_export_leaves_no_temporary_file_behind(session: AsyncSession) -> None:
    user = await _create_user(session)
    problem = await _make_problem(session, user, enabled=True)

    before = set(Path(tempfile.gettempdir()).glob(f"{STAGING_PREFIX}export-*"))
    app = _build_app(session, user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/problems/{problem.arena_number}/export")

    assert response.status_code == 200
    assert set(Path(tempfile.gettempdir()).glob(f"{STAGING_PREFIX}export-*")) == before
