#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.templating import Jinja2Templates
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enumerations import JudgmentStatus, ProblemValidatorType, RoleEnum, Verdict
from web.config import settings
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import UberAdmin, User
from web.routes import generaluser_dashboard
from web.routes.contest_submissions import download_all_sources
from web.routes.session import router as session_router
from web.services.submission_service import write_team_submissions_zip
from web.template_globals import register_template_globals


async def _make_language(
    session: AsyncSession, language_id: str = "python3", source_filename: str = "main.py"
) -> Language:
    language = Language(
        id=language_id,
        name="Python 3.14",
        icon="python",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["python3", "-m", "py_compile", "/sandbox/main.py"],
        run_cmd=["python3", "-u", "/sandbox/main.py"],
        source_filename=source_filename,
        artifact_path="/sandbox/main.py",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


async def _make_user(
    session: AsyncSession,
    contest: Contest,
    uberadmin: UberAdmin,
    *,
    username: str,
    fullname: str,
    role: RoleEnum,
) -> User:
    user = User(
        username=username,
        fullname=fullname,
        role=role,
        contest_id=contest.id,
        created_by_uberadmin_id=uberadmin.id,
    )
    user.password = "TestPass1!"
    session.add(user)
    await session.flush()
    return user


async def _make_problem(
    session: AsyncSession,
    contest: Contest,
    *,
    title: str,
    ordinal: int,
) -> Problem:
    problem = Problem(
        contest_id=contest.id,
        title=title,
        ordinal=ordinal,
        color="#ff0000",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    return problem


async def _make_submission_with_judgment(
    session: AsyncSession,
    *,
    problem: Problem,
    team: User,
    language: Language,
    source_code: str,
    timestamp_minutes: int,
    final_verdict: Verdict | None,
    created_at: datetime | None = None,
    judgment_created_at: datetime | None = None,
) -> Submission:
    submission = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code=source_code,
        source_hash=hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
        source_size_bytes=len(source_code.encode("utf-8")),
        timestamp_seconds=timestamp_minutes * 60,
        created_at=created_at or datetime.now(UTC),
    )
    session.add(submission)
    await session.flush()

    judgment = SubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.DONE,
        autojudge_verdict=final_verdict,
        final_verdict=final_verdict,
        created_at=judgment_created_at or datetime.now(UTC),
        timestamp_seconds=timestamp_minutes * 60,
    )
    session.add(judgment)
    await session.flush()
    await session.execute(
        update(SubmissionJudgment).where(SubmissionJudgment.id == judgment.id).values(final_verdict=final_verdict)
    )
    await session.flush()
    return submission


def _write_statement(problem: Problem, suffix: str, content: str | bytes) -> None:
    statement_path = Path(settings.PROBLEM_STATEMENT_DIR) / f"{problem.id}-statement.{suffix}"
    if isinstance(content, bytes):
        statement_path.write_bytes(content)
    else:
        statement_path.write_text(content, encoding="utf-8")


def _zip_names(payload: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        return set(zf.namelist())


def _build_dashboard_app(ctx: ContestContext) -> FastAPI:
    app = FastAPI()
    templates = Jinja2Templates(directory=Path(__file__).resolve().parents[2] / "web" / "template")
    templates.env.globals["app_version"] = "test"
    templates.env.globals["contest_minutes"] = lambda seconds: None if seconds is None else seconds // 60
    register_template_globals(templates)
    # `_base.html` resolves the keepalive route on every authenticated page.
    app.include_router(session_router)
    templates.env.globals["get_flashed_messages"] = lambda with_categories=False: []
    app.state.templates = templates

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

    @app.get("/profile", name="profile_get")
    async def _profile() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/logout", name="logout")
    async def _logout() -> dict[str, str]:
        return {"ok": "ok"}

    @app.get("/c/{slug}/clock", name="contest_clock")
    @app.get("/c/{slug}/score", name="contest_score")
    @app.get("/c/{slug}/problems", name="contest_problems")
    @app.get("/c/{slug}/clarifications", name="contest_clarifications")
    @app.get("/c/{slug}/runs", name="contest_runs")
    @app.get("/c/{slug}/tasks", name="contest_tasks")
    @app.get("/c/{slug}/submissions/download-all", name="team_submissions_download")
    @app.get("/problem-set/{slug}.zip", name="problem_set_download")
    @app.get("/c/{slug}/reports", name="contest_reports")
    @app.get("/c/{slug}/team-status", name="contest_team_status")
    @app.get("/c/{slug}/solution-tests", name="contest_solution_tests")
    async def _contest_stub(slug: str) -> dict[str, str]:
        return {"slug": slug}

    app.include_router(generaluser_dashboard.router)

    async def _override_ctx() -> ContestContext:
        return ctx

    app.dependency_overrides[get_contest_context] = _override_ctx
    return app


@pytest.mark.asyncio
async def test_build_team_submissions_zip_exports_expected_layout(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
    tmp_path: Path,
) -> None:
    stopped_contest.release_scoreboard_after_end = True
    await session.flush()

    team = await _make_user(
        session,
        stopped_contest,
        uberadmin,
        username="team_export",
        fullname="Team Export",
        role=RoleEnum.TEAM,
    )
    language = await _make_language(session)
    problem_a = await _make_problem(session, stopped_contest, title="Problem A Title", ordinal=1)
    problem_b = await _make_problem(session, stopped_contest, title="Problem B Title", ordinal=2)
    problem_c = await _make_problem(session, stopped_contest, title="Problem C Title", ordinal=3)

    _write_statement(problem_a, "md", "# Problem A\n")
    _write_statement(problem_b, "pdf", b"%PDF-1.4 test")
    _write_statement(problem_c, "md", "# Problem C\n")

    await _make_submission_with_judgment(
        session,
        problem=problem_a,
        team=team,
        language=language,
        source_code="print('wa')\n",
        timestamp_minutes=5,
        final_verdict=Verdict.WA,
    )
    await _make_submission_with_judgment(
        session,
        problem=problem_a,
        team=team,
        language=language,
        source_code="print('pe')\n",
        timestamp_minutes=7,
        final_verdict=Verdict.PE,
    )
    await _make_submission_with_judgment(
        session,
        problem=problem_b,
        team=team,
        language=language,
        source_code="print('ac')\n",
        timestamp_minutes=12,
        final_verdict=Verdict.AC,
    )
    await _make_submission_with_judgment(
        session,
        problem=problem_b,
        team=team,
        language=language,
        source_code="print('wa later')\n",
        timestamp_minutes=20,
        final_verdict=Verdict.WA,
    )

    destination = tmp_path / "submissions.zip"
    filename = await write_team_submissions_zip(
        session,
        stopped_contest,
        team,
        statement_dir=Path(settings.PROBLEM_STATEMENT_DIR),
        destination=destination,
    )

    assert filename == "submissions-stopped-contest-team_export.zip"
    names = _zip_names(destination.read_bytes())
    assert "Problem A/" in names
    assert "Problem A/statement.md" in names
    assert "Problem A/AC/" in names
    assert "Problem A/AC/PE-main.py" in names
    assert "Problem A/Other/0005-WA-main.py" in names
    assert "Problem B/statement.pdf" in names
    assert "Problem B/AC/main.py" in names
    assert "Problem B/Other/0020-WA-main.py" in names
    assert "Problem C/" in names
    assert "Problem C/statement.md" in names
    assert "Problem C/AC/" not in names
    assert "Problem C/Other/" not in names


@pytest.mark.asyncio
async def test_download_all_sources_requires_team_and_released_scoreboard(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    team = await _make_user(
        session,
        stopped_contest,
        uberadmin,
        username="team_route",
        fullname="Team Route",
        role=RoleEnum.TEAM,
    )
    admin = await _make_user(
        session,
        stopped_contest,
        uberadmin,
        username="admin_route",
        fullname="Admin Route",
        role=RoleEnum.ADMIN,
    )
    problem = await _make_problem(session, stopped_contest, title="Problem Route", ordinal=1)
    language = await _make_language(session)
    _write_statement(problem, "md", "# Problem\n")
    await _make_submission_with_judgment(
        session,
        problem=problem,
        team=team,
        language=language,
        source_code="print('ok')\n",
        timestamp_minutes=1,
        final_verdict=Verdict.AC,
    )

    with pytest.raises(HTTPException) as not_released_exc:
        await download_all_sources(ContestContext(contest=stopped_contest, session=session, actor=team))
    assert not_released_exc.value.status_code == 403

    stopped_contest.release_scoreboard_after_end = True
    await session.flush()

    response = await download_all_sources(ContestContext(contest=stopped_contest, session=session, actor=team))
    assert response.media_type == "application/zip"
    assert (
        response.headers["Content-Disposition"] == 'attachment; filename="submissions-stopped-contest-team_route.zip"'
    )
    exported_path = Path(response.path)  # type: ignore[attr-defined]
    assert exported_path.exists()

    async def receive() -> dict[str, str]:
        return {"type": "http.request"}

    async def send(_message: object) -> None:
        return None

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/download",
        "raw_path": b"/download",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
        "root_path": "",
    }
    await response(scope, receive, send)  # type: ignore[arg-type,operator]
    assert not exported_path.exists()

    with pytest.raises(HTTPException) as admin_exc:
        await download_all_sources(ContestContext(contest=stopped_contest, session=session, actor=admin))
    assert admin_exc.value.status_code == 403


@pytest.mark.asyncio
async def test_dashboard_renders_download_tile_disabled_until_release(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    team = await _make_user(
        session,
        stopped_contest,
        uberadmin,
        username="team_dash",
        fullname="Team Dash",
        role=RoleEnum.TEAM,
    )
    ctx = ContestContext(contest=stopped_contest, session=session, actor=team)
    app = _build_dashboard_app(ctx)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/c/{stopped_contest.login_slug}/")

    assert response.status_code == 200
    assert "Download submissions" in response.text
    assert "Only after contest end" in response.text
    assert re.search(r'href="#"[^>]*>[\s\S]*Download submissions', response.text) is not None


@pytest.mark.asyncio
async def test_dashboard_renders_download_tile_active_after_release(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    stopped_contest.release_scoreboard_after_end = True
    await session.flush()
    team = await _make_user(
        session,
        stopped_contest,
        uberadmin,
        username="team_dash_active",
        fullname="Team Dash Active",
        role=RoleEnum.TEAM,
    )
    ctx = ContestContext(contest=stopped_contest, session=session, actor=team)
    app = _build_dashboard_app(ctx)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/c/{stopped_contest.login_slug}/")

    assert response.status_code == 200
    assert "Download submissions" in response.text
    assert f"/c/{stopped_contest.login_slug}/submissions/download-all" in response.text
    # Scoped to this tile: the dashboard carries other gated cards (the problem-set
    # archive), so a page-wide absence check would assert something else entirely.
    tile = response.text.split("submissions/download-all")[1].split("</a>")[0]
    assert "Only after contest end" not in tile


# ---------------------------------------------------------------------------
# Problem-set archive tile
#
# Unlike the submissions download, this one is offered to every contest role:
# the archive it links to is anonymous once released, so the tile only surfaces
# a URL that is already public.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_problem_set_tile_is_disabled_until_released(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """An ended contest whose problem set is withheld shows the tile inert."""
    stopped_contest.release_scoreboard_after_end = True
    await session.flush()
    team = await _make_user(
        session, stopped_contest, uberadmin, username="team_ps", fullname="Team PS", role=RoleEnum.TEAM
    )
    app = _build_dashboard_app(ContestContext(contest=stopped_contest, session=session, actor=team))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/c/{stopped_contest.login_slug}/")

    assert response.status_code == 200
    assert "Problem set archive" in response.text
    # Releasing the scoreboard must not offer the archive -- that is the decoupling.
    assert f"/problem-set/{stopped_contest.login_slug}.zip" not in response.text
    assert "Published only if the organizers release it" in response.text


@pytest.mark.asyncio
async def test_dashboard_problem_set_tile_links_once_released(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """Releasing the problem set turns the tile into a real download link."""
    stopped_contest.release_problem_set_after_end = True
    await session.flush()
    team = await _make_user(
        session, stopped_contest, uberadmin, username="team_ps2", fullname="Team PS2", role=RoleEnum.TEAM
    )
    app = _build_dashboard_app(ContestContext(contest=stopped_contest, session=session, actor=team))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/c/{stopped_contest.login_slug}/")

    assert response.status_code == 200
    assert f"/problem-set/{stopped_contest.login_slug}.zip" in response.text
    tile = response.text.split(f"/problem-set/{stopped_contest.login_slug}.zip")[1].split("</a>")[0]
    assert "Only after contest end" not in tile


@pytest.mark.asyncio
async def test_dashboard_problem_set_tile_is_offered_to_non_team_roles(
    session: AsyncSession,
    stopped_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """Judges get the archive tile even though the submissions one is team-only."""
    stopped_contest.release_problem_set_after_end = True
    await session.flush()
    judge = await _make_user(
        session, stopped_contest, uberadmin, username="judge_ps", fullname="Judge PS", role=RoleEnum.JUDGE
    )
    app = _build_dashboard_app(ContestContext(contest=stopped_contest, session=session, actor=judge))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/c/{stopped_contest.login_slug}/")

    assert response.status_code == 200
    assert f"/problem-set/{stopped_contest.login_slug}.zip" in response.text
    assert "Download submissions" not in response.text


@pytest.mark.asyncio
async def test_dashboard_problem_set_tile_stays_inert_while_a_contest_runs(
    session: AsyncSession,
    running_contest: Contest,
    uberadmin: UberAdmin,
) -> None:
    """An armed but still-running contest must not offer the download."""
    running_contest.release_problem_set_after_end = True
    await session.flush()
    team = await _make_user(
        session, running_contest, uberadmin, username="team_ps3", fullname="Team PS3", role=RoleEnum.TEAM
    )
    app = _build_dashboard_app(ContestContext(contest=running_contest, session=session, actor=team))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/c/{running_contest.login_slug}/")

    assert response.status_code == 200
    assert "Problem set archive" in response.text
    assert f"/problem-set/{running_contest.login_slug}.zip" not in response.text
