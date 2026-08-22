#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Route coverage for the participant-facing contest problem list.

Covers the redesign that dropped the admin-only test-case-count columns and
added a per-problem solving rate plus a team viewer's own solve status.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_flash import setup_flash
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from shared.db_schema import submission_judgments as submission_judgments_table
from shared.enumerations import JudgmentStatus, ProblemValidatorType, RoleEnum, Verdict
from shared.services.scoreboard_projection import ProblemResult
from tests.conftest import _make_user
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem
from web.models.submission import Submission, SubmissionJudgment
from web.models.users import UberAdmin, User
from web.routes import contest_problems
from web.services.scoreboard import ScoreboardSnapshot, TeamStanding
from web.template_globals import template_globals


def _build_app(ctx: ContestContext) -> FastAPI:
    """Build a minimal app that renders the real problem list template."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    web_dir = Path(__file__).resolve().parents[2] / "web"
    shared_dir = Path(__file__).resolve().parents[2] / "shared"
    templates = Jinja2Templates(directory=web_dir / "template")
    templates.env.globals.update(
        {
            "app_version": "test",
            "brand_name": "NOCA",
            "healthmon_url": "",
            **template_globals(),
        }
    )
    setup_flash(templates)
    app.state.templates = templates
    app.state.valkey_runtime = object()

    app.mount("/static/vendor", StaticFiles(directory=shared_dir / "static" / "vendor"), name="static_vendor")
    app.mount("/static/css", StaticFiles(directory=web_dir / "static" / "css"), name="static_css")
    app.mount("/static/js", StaticFiles(directory=web_dir / "static" / "js"), name="static_js")
    app.mount("/static/shared/js", StaticFiles(directory=shared_dir / "static" / "js"), name="static_shared_js")
    app.mount("/static/img", StaticFiles(directory=web_dir / "static" / "img"), name="static_img")

    @app.get("/uberadmin", name="uberadmin_dashboard")
    @app.get("/logout", name="logout")
    @app.get("/profile", name="profile_get")
    async def _account_stub() -> dict[str, str]:
        """Provide account route names used by the base template."""
        return {"ok": "ok"}

    @app.get("/c/{slug}", name="contest_dashboard")
    @app.get("/c/{slug}/clock", name="contest_clock")
    @app.get("/c/{slug}/runs/events", name="contest_runs_events")
    async def _contest_stub(slug: str = "") -> dict[str, str]:
        """Provide contest route names used by the base template and the
        live-refresh wiring (SSE endpoint reused from the Runs page)."""
        return {"slug": slug}

    @app.get("/assets/balloon/{color}", name="balloon")
    async def _balloon_stub(color: str = "") -> dict[str, str]:
        """Provide the single-param balloon route name used by problem cards."""
        return {"color": color}

    app.include_router(contest_problems.router)

    async def _override_ctx() -> ContestContext:
        """Return the requested problem-list actor and contest."""
        return ctx

    app.dependency_overrides[get_contest_context] = _override_ctx
    return app


async def _create_problem(session: AsyncSession, contest: Contest) -> Problem:
    """Create a minimal contest problem for the problem list."""
    problem = Problem(
        contest_id=contest.id,
        title="Two Sum",
        ordinal=1,
        color="#2f9e41",
        validator_type=ProblemValidatorType.STANDARD,
    )
    session.add(problem)
    await session.flush()
    return problem


def _snapshot(problem_label: str, *standings: TeamStanding) -> ScoreboardSnapshot:
    """Build a scoreboard snapshot naming a single problem label."""
    return ScoreboardSnapshot(
        contest_id="contest-1",
        generated_at="2026-07-24T12:00:00Z",
        is_frozen=False,
        standings=list(standings),
        problems=[problem_label],
        balloon_colors=["2f9e41"],
    )


async def _make_language(session: AsyncSession) -> Language:
    """Create a minimal active language for a real (non-mocked) submission."""
    language = Language(
        id=f"pl-{uuid4().hex[:8]}",
        name="Problem List Test Language",
        icon="devicon-python-plain",
        compile_image="noca/test:compile",
        run_image="noca/test:run",
        compile_cmd=["true"],
        run_cmd=["true"],
        source_filename="source.py",
        artifact_path="/sandbox/source.py",
        artifact_is_source=True,
        compile_timeout_s=10.0,
        active=True,
    )
    session.add(language)
    await session.flush()
    return language


async def _make_accepted_submission(
    session: AsyncSession,
    *,
    problem: Problem,
    team: User,
    language: Language,
    timestamp_minutes: int,
) -> Submission:
    """Create a real Accepted submission at a given contest-relative minute."""
    source = f"print('{uuid4().hex}')\n"
    created_at = datetime.now(UTC)
    submission = Submission(
        problem_id=problem.id,
        team_id=team.id,
        language_id=language.id,
        source_code=source,
        source_hash=hashlib.sha256(source.encode()).hexdigest(),
        source_size_bytes=len(source.encode()),
        timestamp_seconds=timestamp_minutes * 60,
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(submission)
    await session.flush()

    judgment = SubmissionJudgment(
        submission_id=submission.id,
        status=JudgmentStatus.DONE,
        autojudge_verdict=Verdict.AC,
        final_verdict=Verdict.AC,
        created_at=created_at,
        timestamp_seconds=timestamp_minutes * 60,
    )
    session.add(judgment)
    await session.flush()

    # The before_flush hook recomputes final_verdict from confirmations (None
    # for a brand-new judgment), overwriting the AC set above. Persist the
    # intended verdict directly so the DB reflects this test's setup. The
    # session's own `expire_on_commit=False` means the identity-mapped
    # `judgment` instance keeps its stale in-memory `None` after this raw
    # bulk update -- any later ORM query for the same row returns that cached
    # instance rather than re-reading the database, so it must be expired
    # explicitly to force the next read to reload the real value.
    await session.execute(
        update(submission_judgments_table)
        .where(submission_judgments_table.c.id == judgment.id)
        .values(final_verdict=Verdict.AC.value)
    )
    session.expire(judgment, ["final_verdict"])
    return submission


async def _get_problems_live(actor: UberAdmin | User, session: AsyncSession, contest: Contest) -> str:
    """Render the problem list through its HTTP route with a real DB-backed snapshot."""
    app = _build_app(ContestContext(contest=contest, session=session, actor=actor))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(f"/c/{contest.login_slug}/problems/")
    assert response.status_code == 200
    return response.text


async def _get_problems(
    actor: UberAdmin | User,
    session: AsyncSession,
    contest: Contest,
    snapshot: ScoreboardSnapshot,
) -> str:
    """Render the problem list through its HTTP route and return the HTML."""
    app = _build_app(ContestContext(contest=contest, session=session, actor=actor))
    with patch.object(
        contest_problems._scoreboard_service,
        "get_cached_or_compute",
        new=AsyncMock(return_value=snapshot),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(f"/c/{contest.login_slug}/problems/")
    assert response.status_code == 200
    return response.text


@pytest.mark.asyncio
async def test_problem_list_drops_test_case_counts_and_shows_solving_rate(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
    another_team_user: User,
) -> None:
    """Solving rate replaces the admin-only TC-count columns for every viewer."""
    problem = await _create_problem(session, running_contest)
    await session.flush()
    label = "A"
    solved = TeamStanding(
        rank=1,
        team_id=team_user.id,
        team_name=team_user.username,
        team_fullname=team_user.fullname,
        problems_solved=1,
        total_time=10,
        problems={
            label: ProblemResult(
                label=label,
                problem_id=problem.id,
                solved=True,
                attempts=0,
                solved_at_minutes=10,
                penalty=0,
                is_pending=False,
            )
        },
    )
    untried = TeamStanding(
        rank=2,
        team_id=another_team_user.id,
        team_name=another_team_user.username,
        team_fullname=another_team_user.fullname,
        problems_solved=0,
        total_time=0,
        problems={
            label: ProblemResult(
                label=label,
                problem_id=problem.id,
                solved=False,
                attempts=0,
                solved_at_minutes=None,
                penalty=0,
                is_pending=False,
            )
        },
    )
    snapshot = _snapshot(label, solved, untried)

    html = await _get_problems(team_user, session, running_contest, snapshot)

    assert "Total TCs" not in html
    assert "Public TCs" not in html
    assert "50% solved" in html
    assert "Accepted" in html


@pytest.mark.asyncio
async def test_admin_viewer_sees_solving_rate_but_no_personal_status(
    session: AsyncSession,
    running_contest: Contest,
    admin_user: User,
    team_user: User,
) -> None:
    """An admin has no personal standing, so no verdict status renders for them."""
    problem = await _create_problem(session, running_contest)
    await session.flush()
    label = "A"
    solved = TeamStanding(
        rank=1,
        team_id=team_user.id,
        team_name=team_user.username,
        team_fullname=team_user.fullname,
        problems_solved=1,
        total_time=10,
        problems={
            label: ProblemResult(
                label=label,
                problem_id=problem.id,
                solved=True,
                attempts=0,
                solved_at_minutes=10,
                penalty=0,
                is_pending=False,
            )
        },
    )
    snapshot = _snapshot(label, solved)

    html = await _get_problems(admin_user, session, running_contest, snapshot)

    assert "Total TCs" not in html
    assert "100% solved" in html
    assert "Accepted" not in html
    assert "Not attempted" not in html


@pytest.mark.asyncio
async def test_frozen_accepted_submission_does_not_leak_on_problem_list(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """A team's own post-freeze Accepted verdict must not surface on their problem list.

    Mirrors the scoreboard's own freeze rule exactly, because both routes derive
    from the same ``ScoreboardService.get_cached_or_compute`` snapshot: a
    submission timestamped after ``stop_updating_scoreboard`` is invisible to a
    "public" viewer (team/staff) even when it belongs to that very team, and only
    an admin/judge viewer (the "admin" viewer role) may see it.
    """
    contest = Contest(
        contest_name="Frozen Problem List Contest",
        contest_url="http://frozen.example.com",
        login_slug="frozen-problem-list",
        start_time=datetime.now(UTC) - timedelta(minutes=30),
        duration_minutes=120,
        stop_answers_after=120,
        stop_updating_scoreboard=10,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
    )
    session.add(contest)
    await session.flush()
    assert contest.is_scoreboard_frozen

    team = _make_user(session, contest, uberadmin, "frozen_team", "Frozen Team", RoleEnum.TEAM)
    admin = _make_user(session, contest, uberadmin, "frozen_admin", "Frozen Admin", RoleEnum.ADMIN)
    await session.flush()

    problem = await _create_problem(session, contest)
    language = await _make_language(session)
    # Freeze boundary is at minute 10; submit well after it, but still within
    # the running contest (30 minutes elapsed, 120-minute duration).
    await _make_accepted_submission(session, problem=problem, team=team, language=language, timestamp_minutes=20)
    await session.commit()

    team_html = await _get_problems_live(team, session, contest)
    admin_html = await _get_problems_live(admin, session, contest)

    assert "Accepted" not in team_html
    assert "0% solved" in team_html
    # An admin never gets a personal status chip (build_problem_cards only
    # populates viewer_status for RoleEnum.TEAM), but the solving rate itself
    # must reflect the unfrozen, live data admin/judge viewers are entitled to.
    assert "100% solved" in admin_html


@pytest.mark.asyncio
async def test_live_refresh_wiring_present_for_team_on_running_contest(
    session: AsyncSession,
    running_contest: Contest,
    team_user: User,
) -> None:
    """A team viewer of a running contest gets the htmx/SSE live-refresh wiring."""
    problem = await _create_problem(session, running_contest)
    await session.flush()
    label = "A"
    solved = TeamStanding(
        rank=1,
        team_id=team_user.id,
        team_name=team_user.username,
        team_fullname=team_user.fullname,
        problems_solved=1,
        total_time=10,
        problems={
            label: ProblemResult(
                label=label,
                problem_id=problem.id,
                solved=True,
                attempts=0,
                solved_at_minutes=10,
                penalty=0,
                is_pending=False,
            )
        },
    )
    snapshot = _snapshot(label, solved)

    html = await _get_problems(team_user, session, running_contest, snapshot)

    assert 'id="problems-list"' in html
    assert "hx-get" in html
    assert "verdict-update" in html
    assert "/runs/events" in html
    assert f'data-problem-id="{problem.id}"' in html
    assert 'data-viewer-status="solved"' in html


@pytest.mark.asyncio
async def test_live_refresh_wiring_absent_for_admin_viewer(
    session: AsyncSession,
    running_contest: Contest,
    admin_user: User,
    team_user: User,
) -> None:
    """An admin has no personal standing to celebrate, so no live-refresh wiring ships."""
    problem = await _create_problem(session, running_contest)
    await session.flush()
    label = "A"
    solved = TeamStanding(
        rank=1,
        team_id=team_user.id,
        team_name=team_user.username,
        team_fullname=team_user.fullname,
        problems_solved=1,
        total_time=10,
        problems={
            label: ProblemResult(
                label=label,
                problem_id=problem.id,
                solved=True,
                attempts=0,
                solved_at_minutes=10,
                penalty=0,
                is_pending=False,
            )
        },
    )
    snapshot = _snapshot(label, solved)

    html = await _get_problems(admin_user, session, running_contest, snapshot)

    assert "hx-get" not in html
    assert "data-sse-url" not in html


@pytest.mark.asyncio
async def test_live_refresh_wiring_absent_once_contest_has_ended(
    session: AsyncSession,
    uberadmin: UberAdmin,
) -> None:
    """A finished contest has nothing left to celebrate live, so the wiring is skipped."""
    contest = Contest(
        contest_name="Ended Problem List Contest",
        contest_url="http://ended.example.com",
        login_slug="ended-problem-list",
        start_time=datetime.now(UTC) - timedelta(hours=3),
        duration_minutes=60,
        stop_answers_after=60,
        stop_updating_scoreboard=60,
        clarifications_timeout_minutes=10,
        created_by_uberadmin_id=uberadmin.id,
        release_scoreboard_after_end=True,
    )
    session.add(contest)
    await session.flush()
    team = _make_user(session, contest, uberadmin, "ended_team", "Ended Team", RoleEnum.TEAM)
    await session.flush()

    problem = await _create_problem(session, contest)
    await session.flush()
    label = "A"
    snapshot = _snapshot(
        label,
        TeamStanding(
            rank=1,
            team_id=team.id,
            team_name=team.username,
            team_fullname=team.fullname,
            problems_solved=0,
            total_time=0,
            problems={
                label: ProblemResult(
                    label=label,
                    problem_id=problem.id,
                    solved=False,
                    attempts=0,
                    solved_at_minutes=None,
                    penalty=0,
                    is_pending=False,
                )
            },
        ),
    )

    html = await _get_problems(team, session, contest, snapshot)

    assert "hx-get" not in html
    assert "data-sse-url" not in html
