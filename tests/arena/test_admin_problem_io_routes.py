#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route tests for Arena admin problem import/export endpoints."""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncGenerator, Callable
from datetime import date
from typing import Any

import anyio
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import arena.models.arena_problems  # noqa: F401
import arena.models.arena_submissions  # noqa: F401
import arena.models.arena_users  # noqa: F401
from arena.database import get_db
from arena.dependencies.admin import require_arena_problem_editor
from arena.models.arena_users import ArenaUser
from arena.routes import admin_problem_io
from arena.services import admin_problem_service
from arena.services.admin_category_service import normalize_slug
from shared.enumerations import ArenaRole


async def _create_admin(session: AsyncSession) -> ArenaUser:
    """Create an Arena admin user for route dependency overrides."""
    user = ArenaUser(
        nome="Export Admin",
        email_normalizado="export-admin@test.example",
        password_hash="hash",
        role=ArenaRole.ARENA_ADMIN,
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
    """Build a minimal app with the problem import/export router."""
    app = FastAPI()

    async def _override_db() -> AsyncGenerator[AsyncSession]:
        yield session

    async def _override_current_user() -> ArenaUser:
        return current_user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_arena_problem_editor] = _override_current_user
    app.include_router(admin_problem_io.router)
    return app


@pytest.mark.asyncio
async def test_problem_export_runs_zip_builder_off_event_loop(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Export route should keep direct download while offloading ZIP generation."""
    admin = await _create_admin(session)
    problem = await admin_problem_service.create_problem(
        session,
        caller_id=admin.id,
        title="Threaded Export",
        source=None,
        hide_author_show_source=False,
        time_limit_ms=1000,
        memory_limit_kb=262144,
        pids_limit=64,
        output_limit_in_bytes=65536,
        problem_statement="# Statement\n",
        image_b64=None,
        image_mime=None,
        image_caption=None,
        notes=None,
        category_ids=[],
    )
    await session.commit()

    called: dict[str, bool] = {"run_sync": False}

    async def _run_sync(func: Callable[..., bytes], *args: Any) -> bytes:
        called["run_sync"] = True
        return func(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", _run_sync)

    app = _build_app(session, admin)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/admin/problems/{problem.id}/export")

    assert called["run_sync"] is True
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    expected_filename = f"problem-{problem.arena_number}-{normalize_slug(problem.title)}.zip"
    assert response.headers["content-disposition"] == f'attachment; filename="{expected_filename}"'

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert "problem.json" in archive.namelist()
    assert archive.read("statement.md").decode("utf-8") == "# Statement\n"
