#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from web.config import settings
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.routes.contest_admin_problem_helpers import _label
from web.routes.contest_runs_helpers import (
    _ALLOWED,
    _access_blocked,
    _build_first_balloon_submission_ids,
    _build_problem_map,
    _build_review_lock_context,
    _html,
    _team_runs_are_blind,
)
from web.routes.contest_runs_helpers import _iter_verdict_sse_events as _iter_verdict_sse_events  # noqa: F401
from web.services.problem_service import get_contest_languages, get_contest_problems
from web.services.submission_service import list_submissions

router = APIRouter(prefix="/c/{slug}/runs", tags=["contest_runs"])


@router.get("/", response_class=HTMLResponse, name="contest_runs")
async def view(
    request: Request,
    ctx: ContestContext = Depends(get_contest_context),
    problem_id: str = "",
) -> HTMLResponse:
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, _ALLOWED)
    access_blocked = _access_blocked(ctx.actor, ctx.contest)

    if access_blocked:
        return _html(
            templates.TemplateResponse(
                request,
                "contest/runs.html",
                {
                    "current_user": ctx.actor,
                    "contest": ctx.contest,
                    "access_blocked": True,
                    "team_runs_are_blind": _team_runs_are_blind(ctx.actor, ctx.contest),
                    "problems": [],
                    "problem_map": {},
                    "languages": [],
                    "submissions": [],
                    "first_balloon_submission_ids": set(),
                    "lock_service_available": request.app.state.valkey_runtime.is_available,
                    "review_lock_map": {},
                    "preselect_problem_id": "",
                    "filter_problem_id": "",
                    "filter_autojudge": "",
                    "filter_final_verdict": "",
                    "filter_team_id": "",
                    "show_compile_run_cmds": settings.SHOW_COMPILE_RUN_CMDS,
                },
            )
        )

    problems = await get_contest_problems(ctx.session, ctx.contest)
    problem_map = {p.id: _label(p.ordinal) for p in problems}
    first_balloon_submission_ids = await _build_first_balloon_submission_ids(ctx.session, ctx.contest)
    problem_labels = {p.id: f"{_label(p.ordinal)}: {p.title}" for p in problems}
    languages = await get_contest_languages(ctx.session, ctx.contest)
    lang_map = {lang.id: lang.name for lang in languages}
    lang_icon_map = {lang.id: lang.icon for lang in languages}
    # File-picker hint: extension derived from each language's source filename
    # (e.g. "Main.java" -> ".java"), used to filter the submit file input.
    lang_ext_map = {lang.id: PurePosixPath(lang.source_filename).suffix for lang in languages}

    # Build per-problem per-language limits for JS confirmation modal.
    # Structure: {problem_id: {default: {...}, language_id: {...}, ...}}
    submit_limits: dict[str, dict[str, dict[str, object]]] = {}
    for p in problems:
        entry: dict[str, dict[str, object]] = {
            "default": {
                "time_ms": p.time_limit_ms,
                "memory_kb": p.memory_limit_kb,
                "pids": p.pids_limit,
                "output_bytes": p.output_limit_in_bytes,
            }
        }
        for lim in p.language_limits:
            entry[lim.language_id] = {
                "time_ms": lim.time_limit_ms,
                "memory_kb": lim.memory_limit_kb,
                "pids": lim.pids_limit,
                "output_bytes": lim.output_limit_in_bytes,
            }
        submit_limits[p.id] = entry

    submissions = await list_submissions(ctx.session, ctx.contest, ctx.actor)
    lock_service_available, review_lock_map = await _build_review_lock_context(request, ctx.contest, submissions)

    return _html(
        templates.TemplateResponse(
            request,
            "contest/runs.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "access_blocked": False,
                "team_runs_are_blind": _team_runs_are_blind(ctx.actor, ctx.contest),
                "problems": problems,
                "problem_map": problem_map,
                "languages": languages,
                "lang_map": lang_map,
                "lang_icon_map": lang_icon_map,
                "lang_ext_map": lang_ext_map,
                "problem_labels": problem_labels,
                "submit_limits": submit_limits,
                "submissions": submissions,
                "first_balloon_submission_ids": first_balloon_submission_ids,
                "lock_service_available": lock_service_available,
                "review_lock_map": review_lock_map,
                "preselect_problem_id": problem_id,
                "filter_problem_id": "",
                "filter_autojudge": "",
                "filter_final_verdict": "",
                "filter_team_id": "",
                "show_compile_run_cmds": settings.SHOW_COMPILE_RUN_CMDS,
            },
        )
    )


@router.get("/list", response_class=HTMLResponse, name="contest_runs_list")
async def list_partial(
    request: Request,
    ctx: ContestContext = Depends(get_contest_context),
    filter_problem_id: str = Query(""),
    filter_autojudge: str = Query(""),
    filter_final_verdict: str = Query(""),
    filter_team_id: str = Query(""),
) -> HTMLResponse:
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, _ALLOWED)

    if _access_blocked(ctx.actor, ctx.contest):
        return _html(
            templates.TemplateResponse(
                request,
                "contest/runs_list.html",
                {
                    "current_user": ctx.actor,
                    "contest": ctx.contest,
                    "team_runs_are_blind": _team_runs_are_blind(ctx.actor, ctx.contest),
                    "problem_map": {},
                    "submissions": [],
                    "first_balloon_submission_ids": set(),
                    "lock_service_available": request.app.state.valkey_runtime.is_available,
                    "review_lock_map": {},
                    "filter_problem_id": "",
                    "filter_autojudge": "",
                    "filter_final_verdict": "",
                    "filter_team_id": "",
                },
            )
        )

    problem_map = await _build_problem_map(ctx.session, ctx.contest)
    submissions = await list_submissions(ctx.session, ctx.contest, ctx.actor)
    first_balloon_submission_ids = await _build_first_balloon_submission_ids(ctx.session, ctx.contest)
    lock_service_available, review_lock_map = await _build_review_lock_context(request, ctx.contest, submissions)

    return _html(
        templates.TemplateResponse(
            request,
            "contest/runs_list.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "team_runs_are_blind": _team_runs_are_blind(ctx.actor, ctx.contest),
                "problem_map": problem_map,
                "submissions": submissions,
                "first_balloon_submission_ids": first_balloon_submission_ids,
                "lock_service_available": lock_service_available,
                "review_lock_map": review_lock_map,
                "filter_problem_id": filter_problem_id,
                "filter_autojudge": filter_autojudge,
                "filter_final_verdict": filter_final_verdict,
                "filter_team_id": filter_team_id,
            },
        )
    )


@router.get("/language-info", response_class=HTMLResponse, name="contest_runs_language_info")
async def language_info_partial(
    request: Request,
    ctx: ContestContext = Depends(get_contest_context),
    language_id: str = Query(""),
) -> HTMLResponse:
    """Return compile/run command info for a contest language as an HTMX partial."""
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, _ALLOWED)

    language = None
    if language_id:
        languages = await get_contest_languages(ctx.session, ctx.contest)
        lang_map = {lang.id: lang for lang in languages}
        language = lang_map.get(language_id)

    return _html(
        templates.TemplateResponse(
            request,
            "contest/runs_language_info.html",
            {"contest": ctx.contest, "language": language},
        )
    )
