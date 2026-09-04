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
import arena.routes.problems as arena_routes_problems
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


# ── The on-disk cache (#204) ──────────────────────────────────────────────────

_BUILD = "arena.routes.problems.admin_problem_io_service.export_problem_package"
_BUILD_SAMPLES = "arena.routes.problems.problem_tc_export_service.build_sample_testcases_zip"


def _configure_cache(monkeypatch: pytest.MonkeyPatch, cache_root: Path | None) -> None:
    from shared.enumerations import Environment

    monkeypatch.setattr(arena_settings, "PUBLIC_PROBLEM_PACK_PATH", cache_root)
    monkeypatch.setattr(arena_settings, "ENVIRONMENT", Environment.DEVELOPMENT)


def _set_export_budget(monkeypatch: pytest.MonkeyPatch, *, max_requests: int = 10, enabled: bool = True) -> None:
    from arena.dependencies import problem_export_rate_limit as limiter

    monkeypatch.setattr(limiter.settings, "PROBLEM_EXPORT_RATE_LIMIT_ENABLED", enabled)
    monkeypatch.setattr(limiter.settings, "PROBLEM_EXPORT_RATE_LIMIT_MAX_REQUESTS", max_requests)
    monkeypatch.setattr(limiter.settings, "PROBLEM_EXPORT_RATE_LIMIT_WINDOW_SECONDS", 600)


async def _bump(session: AsyncSession, problem: object) -> None:
    """Bump the counter the way an editor action does, then make the shared session see it.

    The route reads the generation off the ORM instance its query returns. In
    production that is a fresh per-request session; this harness hands every
    request the test session, whose identity map still holds the pre-bump value,
    so the instance is refreshed the way a new session would load it.
    """
    from shared.services.public_export_generation import bump_public_export_generation

    await bump_public_export_generation(session, "arena", problem.id)  # type: ignore[attr-defined]
    await session.commit()
    await session.refresh(problem, attribute_names=["public_export_generation"])


@pytest.mark.asyncio
async def test_a_second_export_is_served_from_cache_without_rebuilding(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole point: repetition costs a cache read, not a package build."""
    from unittest.mock import patch

    from shared.services.problem_export_cache import cached_export_path, export_cache_dir

    _configure_cache(monkeypatch, tmp_path)
    _set_export_budget(monkeypatch)
    user = await _create_user(session)
    problem = await _make_problem(session, user, enabled=True)
    app = _build_app(session, user)

    real = arena_routes_problems.admin_problem_io_service.export_problem_package
    with patch(_BUILD, side_effect=real) as build:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            first = await client.get(f"/problems/{problem.arena_number}/export")
            second = await client.get(f"/problems/{problem.arena_number}/export")

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.content == second.content
    assert build.call_count == 1
    assert cached_export_path(export_cache_dir(tmp_path), problem.id).is_file()


@pytest.mark.asyncio
async def test_a_bumped_generation_invalidates_the_cached_export(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import patch

    _configure_cache(monkeypatch, tmp_path)
    _set_export_budget(monkeypatch)
    user = await _create_user(session)
    problem = await _make_problem(session, user, enabled=True)
    app = _build_app(session, user)

    real = arena_routes_problems.admin_problem_io_service.export_problem_package
    with patch(_BUILD, side_effect=real) as build:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            await client.get(f"/problems/{problem.arena_number}/export")
            await _bump(session, problem)
            await client.get(f"/problems/{problem.arena_number}/export")

    assert build.call_count == 2


@pytest.mark.asyncio
async def test_the_sample_zip_is_cached_under_its_own_key_and_shares_the_generation(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two artifacts, one counter: distinct files, both dropped by a single bump."""
    from unittest.mock import patch

    from shared.services.problem_export_cache import SAMPLE_CASES_SUFFIX, cached_export_path, export_cache_dir

    _configure_cache(monkeypatch, tmp_path)
    _set_export_budget(monkeypatch)
    user = await _create_user(session)
    problem = await _make_problem(session, user, enabled=True)
    app = _build_app(session, user)
    cache_dir = export_cache_dir(tmp_path)

    real = arena_routes_problems.problem_tc_export_service.build_sample_testcases_zip
    with patch(_BUILD_SAMPLES, side_effect=real) as build:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            first = await client.get(f"/problems/{problem.arena_number}/sample-testcases.zip")
            again = await client.get(f"/problems/{problem.arena_number}/sample-testcases.zip")
            await client.get(f"/problems/{problem.arena_number}/export")
            await _bump(session, problem)
            after = await client.get(f"/problems/{problem.arena_number}/sample-testcases.zip")

    assert (first.status_code, again.status_code, after.status_code) == (200, 200, 200)
    assert build.call_count == 2  # once cold, once after the bump; the second hit was a cache read
    assert cached_export_path(cache_dir, problem.id, suffix=SAMPLE_CASES_SUFFIX).is_file()
    assert cached_export_path(cache_dir, problem.id).is_file()
    with zipfile.ZipFile(io.BytesIO(first.content)) as archive:
        assert any(name.startswith("in/") for name in archive.namelist())


@pytest.mark.asyncio
async def test_the_export_budget_is_charged_even_on_cache_hits(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from arena.dependencies.problem_export_rate_limit import PROBLEM_EXPORT_DETAIL

    _configure_cache(monkeypatch, tmp_path)
    _set_export_budget(monkeypatch, max_requests=2)
    user = await _create_user(session)
    problem = await _make_problem(session, user, enabled=True)
    app = _build_app(session, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        first = await client.get(f"/problems/{problem.arena_number}/export")
        second = await client.get(f"/problems/{problem.arena_number}/sample-testcases.zip")
        third = await client.get(f"/problems/{problem.arena_number}/export")

    assert (first.status_code, second.status_code) == (200, 200)
    # One budget covers both downloads: the two routes share the bucket.
    assert third.status_code == 429
    assert third.json() == {"detail": PROBLEM_EXPORT_DETAIL}
    assert int(third.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_production_without_a_cache_path_is_503_before_any_query(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every download is refused in that state, so nothing is looked up at all."""
    from typing import Any

    from sqlalchemy import event

    from arena.routes.problems import UNCACHED_IN_PRODUCTION_DETAIL
    from shared.enumerations import Environment

    _set_export_budget(monkeypatch)
    monkeypatch.setattr(arena_settings, "PUBLIC_PROBLEM_PACK_PATH", None)
    monkeypatch.setattr(arena_settings, "ENVIRONMENT", Environment.PRODUCTION)
    user = await _create_user(session)
    problem = await _make_problem(session, user, enabled=True)
    app = _build_app(session, user)

    statements: list[str] = []

    def _capture(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        statements.append(statement)

    engine = session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            export = await client.get(f"/problems/{problem.arena_number}/export")
            samples = await client.get(f"/problems/{problem.arena_number}/sample-testcases.zip")
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert export.status_code == 503
    assert export.json() == {"detail": UNCACHED_IN_PRODUCTION_DETAIL}
    assert samples.status_code == 503
    assert statements == []
