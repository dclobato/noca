#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Non-scoring solution tests for judges and admins.

JUDGE, ADMIN, and UBERADMIN actors upload a candidate solution for any problem
in an active contest and have it judged by the real compiler, sandbox, limits,
test cases, and custom validator — with zero effect on the competition. Nothing
here publishes a submission or verdict event.
"""

from __future__ import annotations

import hashlib
from typing import cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy import select

from shared.enumerations import RoleEnum
from shared.http_params import PageNumber
from web.config import settings
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.models.problem import Problem
from web.models.solution_test import SolutionTestRun
from web.routes.contest_submissions_helpers import submission_highlight_assets
from web.services.problem_service import get_contest_languages
from web.services.solution_test_service import (
    RUNS_PER_PAGE,
    SolutionTestRateLimitError,
    create_solution_test_run,
    enqueue_solution_test_job,
    get_solution_test_run,
    list_solution_test_runs_paginated,
)
from web.services.user_read_rate_limit import web_user_read_rate_limit

router = APIRouter(
    prefix="/c/{slug}/solution-tests", tags=["contest_solution_tests"], dependencies=[Depends(web_user_read_rate_limit)]
)

_ALLOWED = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE)


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


def _own_runs_only(ctx: ContestContext) -> str | None:
    """Return the actor id a JUDGE is restricted to, or None for full visibility.

    ADMIN and UBERADMIN see every run in the contest; a JUDGE sees only their own.
    """
    if ctx.actor.role == RoleEnum.JUDGE:
        return str(ctx.actor.id)
    return None


@router.get("/", response_class=HTMLResponse, name="contest_solution_tests")
async def view(
    request: Request,
    ctx: ContestContext = Depends(get_contest_context),
    page: PageNumber = 1,
    problem_id: str = Query(""),
) -> HTMLResponse:
    """Render the solution-test form and the actor's visible run history."""
    ensure_allowed_role(ctx.actor, _ALLOWED)
    templates = request.app.state.templates

    problems = (
        (
            await ctx.session.execute(
                select(Problem).where(Problem.contest_id == ctx.contest.id).order_by(Problem.ordinal)
            )
        )
        .scalars()
        .all()
    )
    languages = await get_contest_languages(ctx.session, ctx.contest)
    runs = await list_solution_test_runs_paginated(
        ctx.session,
        ctx.contest,
        page=page,
        per_page=RUNS_PER_PAGE,
        problem_id=problem_id.strip() or None,
        restrict_to_user_id=_own_runs_only(ctx),
    )

    return _html(
        templates.TemplateResponse(
            request,
            "contest/solution_tests.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "problems": list(problems),
                "languages": languages,
                "runs": runs,
                "selected_problem_id": problem_id.strip(),
            },
        )
    )


@router.post("/submit", response_class=HTMLResponse, response_model=None, name="contest_solution_tests_submit")
async def submit(
    request: Request,
    flash: FlashDep,
    ctx: ContestContext = Depends(get_contest_context),
    problem_id: str = Form(""),
    language_id: str = Form(""),
    source_file: UploadFile = File(...),
) -> Response:
    """Validate an uploaded candidate solution and queue it for a real judging run."""
    ensure_allowed_role(ctx.actor, _ALLOWED)
    slug = ctx.contest.login_slug
    redirect = RedirectResponse(url=f"/c/{slug}/solution-tests/", status_code=303)

    problem_id = problem_id.strip()
    language_id = language_id.strip()
    if not problem_id or not language_id:
        flash("A problem and a language must be selected.", FlashCategory.DANGER)
        return redirect

    problem = (
        await ctx.session.execute(select(Problem).where(Problem.id == problem_id, Problem.contest_id == ctx.contest.id))
    ).scalar_one_or_none()
    if problem is None:
        flash("The selected problem does not belong to this contest.", FlashCategory.DANGER)
        return redirect

    contest_lang_ids = {lang.id for lang in await get_contest_languages(ctx.session, ctx.contest)}
    if language_id not in contest_lang_ids:
        flash("The selected language is not available for this contest.", FlashCategory.DANGER)
        return redirect

    source_bytes = await source_file.read()
    if not source_bytes:
        flash("The uploaded file is empty.", FlashCategory.DANGER)
        return redirect

    max_size = ctx.contest.max_problem_file_size_bytes
    if max_size > 0 and len(source_bytes) > max_size:
        flash(f"Source file exceeds the maximum allowed size of {max_size} bytes.", FlashCategory.DANGER)
        return redirect

    # Reject binary uploads. NUL bytes are valid UTF-8 and survive decoding with
    # errors="replace", but PostgreSQL cannot store them in text columns.
    if b"\x00" in source_bytes:
        flash("The uploaded file is not a text source file.", FlashCategory.DANGER)
        return redirect

    try:
        run = await create_solution_test_run(
            ctx.session,
            ctx.actor,
            ctx.contest,
            problem_id=problem_id,
            language_id=language_id,
            source_code=source_bytes.decode("utf-8", errors="replace"),
            source_hash=hashlib.sha256(source_bytes).hexdigest(),
            source_size=len(source_bytes),
            rate_limit_window_seconds=settings.WEB_SUBMISSION_RATE_LIMIT_WINDOW_SECONDS,
            rate_limit_max_runs=settings.WEB_SUBMISSION_RATE_LIMIT_MAX_SUBMISSIONS,
        )
    except SolutionTestRateLimitError as exc:
        flash(
            f"Too many solution tests. Try again after {exc.next_allowed_at:%H:%M:%S} UTC.",
            FlashCategory.WARNING,
        )
        return redirect
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return redirect

    run_id = run.id
    await ctx.session.commit()
    await enqueue_solution_test_job(
        request.app.state.valkey_runtime,
        run,
        ctx.contest,
        priority=ctx.contest.is_running,
    )
    flash("Solution test queued.", FlashCategory.SUCCESS)
    return RedirectResponse(url=f"/c/{slug}/solution-tests/{run_id}", status_code=303)


async def _load_visible_run(ctx: ContestContext, run_id: str) -> SolutionTestRun:
    """Load a run the actor may see, or 404.

    A JUDGE opening another judge's run gets 404 rather than 403 so the run's
    existence does not leak.
    """
    run = await get_solution_test_run(
        ctx.session,
        ctx.contest,
        run_id,
        restrict_to_user_id=_own_runs_only(ctx),
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Solution test run not found.")
    return run


@router.get("/{run_id}", response_class=HTMLResponse, name="contest_solution_test_detail")
async def detail(
    request: Request,
    run_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> HTMLResponse:
    """Render one solution-test run with its per-case results."""
    ensure_allowed_role(ctx.actor, _ALLOWED)
    run = await _load_visible_run(ctx, run_id)
    highlight_assets = submission_highlight_assets(run.language.id)
    return _html(
        request.app.state.templates.TemplateResponse(
            request,
            "contest/solution_test_detail.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "run": run,
                "highlight_theme_path": "highlight/styles/github.min.css",
                "highlight_core_path": "highlight/highlight.min.js",
                "highlight_line_numbers_path": "highlight/plugins/highlightjs-line-numbers.min.js",
                **highlight_assets,
            },
        )
    )


@router.get("/{run_id}/status", response_class=HTMLResponse, name="contest_solution_test_status_partial")
async def status_partial(
    request: Request,
    run_id: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> HTMLResponse:
    """Return the status panel for the self-terminating HTMX poll."""
    ensure_allowed_role(ctx.actor, _ALLOWED)
    run = await _load_visible_run(ctx, run_id)
    return _html(
        request.app.state.templates.TemplateResponse(
            request,
            "contest/_solution_test_status.html",
            {"current_user": ctx.actor, "contest": ctx.contest, "run": run},
        )
    )
