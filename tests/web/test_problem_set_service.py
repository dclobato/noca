#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Tests for the public post-contest problem-set archive and its route."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from shared.enumerations import ProblemValidatorType
from web.config import settings
from web.models.contest import Contest
from web.models.problem import Problem, ProblemTestCase
from web.models.users import UberAdmin
from web.routes.problem_set import router as problem_set_router
from web.services.problem_service.files import save_md_statement, save_testcase_files
from web.services.problem_set_cache import cached_archive_path, ensure_cached_archive
from web.services.problem_set_service import (
    _problem_label,
    build_problem_set_archive,
    problem_set_filename,
)


async def _make_problem(
    session: AsyncSession,
    contest: Contest,
    *,
    title: str,
    ordinal: int,
    editorial: str | None = None,
) -> Problem:
    problem = Problem(
        contest_id=contest.id,
        title=title,
        ordinal=ordinal,
        color="#ff0000",
        validator_type=ProblemValidatorType.STANDARD,
        editorial=editorial,
    )
    session.add(problem)
    await session.flush()
    return problem


async def _seed_standard_problem(
    session: AsyncSession,
    problem: Problem,
    *,
    secret_cases: int = 1,
) -> None:
    """Persist the statement, test-case rows, and files a full export requires."""
    save_md_statement(problem.id, f"# {problem.title}\n", settings.PROBLEM_STATEMENT_DIR)
    for ordinal in range(1, secret_cases + 2):
        session.add(
            ProblemTestCase(
                problem_id=problem.id,
                ordinal=ordinal,
                is_sample=ordinal == 1,
                input_size_bytes=2,
                output_size_bytes=4,
            )
        )
        save_testcase_files(problem.id, ordinal, f"{ordinal}\n".encode(), b"out\n", settings.PROBLEM_TESTCASE_DIR)
    await session.flush()


def _zip_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


def _read_member(path: Path, member: str) -> bytes:
    with zipfile.ZipFile(path) as zf:
        return zf.read(member)


def _build_app(session: AsyncSession) -> FastAPI:
    app = FastAPI()
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    app.include_router(problem_set_router)
    return app


def test_problem_label_is_bijective_base26() -> None:
    assert _problem_label(1) == "A"
    assert _problem_label(26) == "Z"
    assert _problem_label(27) == "AA"


def test_problem_set_filename_is_slug_derived(stopped_contest: Contest) -> None:
    assert problem_set_filename(stopped_contest) == "problem-set-stopped-contest.zip"


@pytest.mark.asyncio
async def test_archive_carries_full_packages_with_secret_cases_and_editorials(
    session: AsyncSession,
    stopped_contest: Contest,
    tmp_path: Path,
) -> None:
    problem_a = await _make_problem(session, stopped_contest, title="Alpha", ordinal=1, editorial="# Hint\nRead it.")
    problem_b = await _make_problem(session, stopped_contest, title="Beta", ordinal=2)
    await _seed_standard_problem(session, problem_a)
    await _seed_standard_problem(session, problem_b)
    # The eager-loaded collections were populated when the problems were first
    # flushed (empty test_cases); drop them so the export reloads the new rows.
    session.expire(problem_a, ["test_cases"])
    session.expire(problem_b, ["test_cases"])

    dest = tmp_path / "set.zip"
    await build_problem_set_archive(
        session,
        stopped_contest,
        dest,
        testcase_dir=Path(settings.PROBLEM_TESTCASE_DIR),
        statement_dir=Path(settings.PROBLEM_STATEMENT_DIR),
    )

    names = _zip_names(dest)
    assert "index.json" in names
    # Secret (non-sample) cases must be present, unlike the public profile.
    assert "problems/001-A/in/002.in" in names
    assert "problems/001-A/out/002.out" in names
    assert "problems/001-A/statement.md" in names
    assert "problems/001-A/editorial.md" in names
    assert "problems/001-A/problem.json" in names
    assert "problems/002-B/problem.json" in names
    assert "problems/002-B/editorial.md" not in names

    index = json.loads(_read_member(dest, "index.json"))
    assert index["kind"] == "problem_set"
    assert index["contest"]["slug"] == stopped_contest.login_slug
    assert index["problems"] == [
        {"label": "A", "title": "Alpha", "dir": "problems/001-A"},
        {"label": "B", "title": "Beta", "dir": "problems/002-B"},
    ]

    problem_json = json.loads(_read_member(dest, "problems/001-A/problem.json"))
    editorial_spec = problem_json["editorial"]
    assert editorial_spec is not None and editorial_spec["member"] == "editorial.md"
    assert _read_member(dest, "problems/001-A/editorial.md").decode() == "# Hint\nRead it."
    assert json.loads(_read_member(dest, "problems/002-B/problem.json"))["editorial"] is None


@pytest.mark.asyncio
async def test_interactive_problem_without_validator_source_still_exports(
    session: AsyncSession,
    stopped_contest: Contest,
    tmp_path: Path,
) -> None:
    problem = Problem(
        contest_id=stopped_contest.id,
        title="Interact",
        ordinal=1,
        color="#ff0000",
        # Created interactive from the start: the stored strategy is immutable
        # once the row is persistent.
        validator_type=ProblemValidatorType.INTERACTIVE,
    )
    session.add(problem)
    await session.flush()
    save_md_statement(problem.id, "# Interact\n", settings.PROBLEM_STATEMENT_DIR)
    test_case = ProblemTestCase(problem_id=problem.id, ordinal=1, is_sample=False, input_size_bytes=2)
    session.add(test_case)
    await session.flush()
    save_testcase_files(problem.id, 1, b"1\n", None, settings.PROBLEM_TESTCASE_DIR)
    session.expire(problem, ["test_cases"])

    dest = tmp_path / "set.zip"
    await build_problem_set_archive(
        session,
        stopped_contest,
        dest,
        testcase_dir=Path(settings.PROBLEM_TESTCASE_DIR),
        statement_dir=Path(settings.PROBLEM_STATEMENT_DIR),
    )

    names = _zip_names(dest)
    assert "problems/001-A/in/001.in" in names
    assert "problems/001-A/problem.json" in names


@pytest.mark.asyncio
async def test_route_is_anonymous_and_gated(
    session: AsyncSession,
    stopped_contest: Contest,
    running_contest: Contest,
) -> None:
    problem = await _make_problem(session, stopped_contest, title="Alpha", ordinal=1)
    await _seed_standard_problem(session, problem)
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        missing = await client.get("/problem-set/no-such-contest.zip")
        running = await client.get(f"/problem-set/{running_contest.login_slug}.zip")
        unreleased = await client.get(f"/problem-set/{stopped_contest.login_slug}.zip")

        stopped_contest.release_scoreboard_after_end = True
        await session.commit()
        released = await client.get(f"/problem-set/{stopped_contest.login_slug}.zip")

    assert missing.status_code == 404
    assert running.status_code == 404
    assert unreleased.status_code == 404
    assert released.status_code == 200
    assert released.headers["content-type"] == "application/zip"
    assert released.headers["content-disposition"] == 'attachment; filename="problem-set-stopped-contest.zip"'
    payload = zipfile.ZipFile(io.BytesIO(released.content))
    assert "index.json" in payload.namelist()
    assert "problems/001-A/problem.json" in payload.namelist()


@pytest.mark.asyncio
async def test_route_hides_inactive_contest(
    session: AsyncSession,
    stopped_contest: Contest,
) -> None:
    stopped_contest.active = False
    stopped_contest.release_scoreboard_after_end = True
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/problem-set/{stopped_contest.login_slug}.zip")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_route_answers_409_when_a_statement_file_is_missing(
    session: AsyncSession,
    stopped_contest: Contest,
) -> None:
    stopped_contest.release_scoreboard_after_end = True
    await _make_problem(session, stopped_contest, title="Broken", ordinal=1)
    await session.commit()

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/problem-set/{stopped_contest.login_slug}.zip")

    assert response.status_code == 409
    assert "no statement file" in response.json()["detail"]


@pytest.mark.asyncio
async def test_contests_page_shows_problem_set_button_only_after_release(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    from fastapi.templating import Jinja2Templates

    from shared.enumerations import RoleEnum
    from web.routes.root import router as root_router

    app = FastAPI()
    app.state.db_session = async_sessionmaker(session.bind, expire_on_commit=False)
    templates = Jinja2Templates(directory=Path(__file__).resolve().parents[2] / "web" / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["brand_name"] = "NOCA"
    templates.env.globals["RoleEnum"] = RoleEnum
    templates.env.globals["role_labels"] = {role.value: role.value.title() for role in RoleEnum}
    templates.env.globals["get_flashed_messages"] = lambda with_categories=False: []
    app.state.templates = templates
    app.include_router(root_router)
    app.include_router(problem_set_router)

    @app.get("/c/{slug}/login", name="contest_login_get")
    async def _login(slug: str) -> dict[str, str]:
        return {"slug": slug}

    @app.get("/login", name="login_get")
    async def _uber_login() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/static/vendor/{path:path}", name="static_vendor")
    async def _static_vendor(path: str) -> dict[str, str]:
        return {"path": path}

    @app.get("/static/css/{path:path}", name="static_css")
    async def _static_css(path: str) -> dict[str, str]:
        return {"path": path}

    @app.get("/static/js/{path:path}", name="static_js")
    async def _static_js(path: str) -> dict[str, str]:
        return {"path": path}

    @app.get("/static/shared/js/{path:path}", name="static_shared_js")
    async def _static_shared_js(path: str) -> dict[str, str]:
        return {"path": path}

    @app.get("/static/img/{path:path}", name="static_img")
    async def _static_img(path: str) -> dict[str, str]:
        return {"path": path}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        before = await client.get("/contests")
        stopped_contest.release_scoreboard_after_end = True
        await session.commit()
        after = await client.get("/contests")

    assert before.status_code == 200
    assert "Problem set" not in before.text
    assert after.status_code == 200
    assert f"/problem-set/{stopped_contest.login_slug}.zip" in after.text
    assert "Problem set" in after.text


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------


def _write_fake_archive(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)


async def _fake_builder(payload: bytes) -> Callable[[Path], Awaitable[None]]:
    async def _build(dest: Path) -> None:
        _write_fake_archive(dest, payload)

    return _build


@pytest.mark.asyncio
async def test_cache_builds_once_and_reuses_verified_archive(
    stopped_contest: Contest,
    tmp_path: Path,
) -> None:
    builds = 0

    async def _build(dest: Path) -> None:
        nonlocal builds
        builds += 1
        _write_fake_archive(dest, b"zip-bytes")

    first = await ensure_cached_archive(tmp_path, stopped_contest, _build)
    second = await ensure_cached_archive(tmp_path, stopped_contest, _build)

    assert first == second == cached_archive_path(tmp_path, stopped_contest)
    assert first.name == "problem-set-stopped-contest.zip"
    assert builds == 1
    assert first.read_bytes() == b"zip-bytes"
    sidecar = tmp_path / "problem-set-stopped-contest.zip.sha256"
    assert sidecar.read_text(encoding="ascii") == hashlib.sha256(b"zip-bytes").hexdigest()


@pytest.mark.asyncio
async def test_cache_rebuilds_when_sidecar_is_missing_or_mismatched(
    stopped_contest: Contest,
    tmp_path: Path,
) -> None:
    archive_path = cached_archive_path(tmp_path, stopped_contest)
    archive_path.write_bytes(b"stale")

    # No sidecar at all: rebuild.
    result = await ensure_cached_archive(tmp_path, stopped_contest, await _fake_builder(b"fresh"))
    assert result.read_bytes() == b"fresh"

    # Sidecar present but not matching the file: rebuild.
    archive_path.write_bytes(b"tampered")
    result = await ensure_cached_archive(tmp_path, stopped_contest, await _fake_builder(b"rebuilt"))
    assert result.read_bytes() == b"rebuilt"


@pytest.mark.asyncio
async def test_cache_serializes_concurrent_builds(
    stopped_contest: Contest,
    tmp_path: Path,
) -> None:
    builds = 0

    async def _build(dest: Path) -> None:
        nonlocal builds
        builds += 1
        await asyncio.sleep(0.05)
        _write_fake_archive(dest, b"zip-bytes")

    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(ensure_cached_archive(tmp_path, stopped_contest, _build)) for _ in range(5)]

    assert builds == 1
    assert {task.result() for task in tasks} == {cached_archive_path(tmp_path, stopped_contest)}


@pytest.mark.asyncio
async def test_cache_failed_build_leaves_no_partials(
    stopped_contest: Contest,
    tmp_path: Path,
) -> None:
    async def _broken(dest: Path) -> None:
        dest.write_bytes(b"partial")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await ensure_cached_archive(tmp_path, stopped_contest, _broken)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_route_serves_from_cache_when_configured(
    session: AsyncSession,
    stopped_contest: Contest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = await _make_problem(session, stopped_contest, title="Alpha", ordinal=1)
    await _seed_standard_problem(session, problem)
    stopped_contest.release_scoreboard_after_end = True
    await session.commit()
    monkeypatch.setattr(settings, "PUBLIC_PROBLEM_PACK_PATH", tmp_path)

    app = _build_app(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        first = await client.get(f"/problem-set/{stopped_contest.login_slug}.zip")
        cached_zip = cached_archive_path(tmp_path, stopped_contest)
        assert cached_zip.is_file()

        # Corrupt the cache on disk: the digest check must refuse it and rebuild.
        cached_zip.write_bytes(b"tampered")
        second = await client.get(f"/problem-set/{stopped_contest.login_slug}.zip")

        # Un-releasing the contest stops serving even though a cache entry exists.
        stopped_contest.release_scoreboard_after_end = False
        await session.commit()
        third = await client.get(f"/problem-set/{stopped_contest.login_slug}.zip")

    assert first.status_code == 200
    assert (
        zipfile.ZipFile(io.BytesIO(first.content)).namelist() == zipfile.ZipFile(io.BytesIO(second.content)).namelist()
    )
    assert cached_zip.read_bytes() != b"tampered"
    assert third.status_code == 404
