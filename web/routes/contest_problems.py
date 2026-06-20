#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from pathlib import PurePosixPath
from typing import cast

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from shared.enumerations import RoleEnum
from web.config import settings
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest
from web.models.problem import Problem
from web.models.users import UberAdmin, User
from web.routes.contest_admin_problem_helpers import _label
from web.services.problem_service import (
    build_public_export_zip,
    get_active_statement_path,
    get_contest_languages,
    get_contest_problems,
    read_testcase_full,
)

router = APIRouter(prefix="/c/{slug}/problems", tags=["contest_problems"])


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


def _prestart_access_blocked(actor: UberAdmin | User, contest: Contest) -> bool:
    role = actor.role
    if role in (RoleEnum.UBERADMIN, RoleEnum.ADMIN):
        return False
    if role == RoleEnum.JUDGE:
        return False
    if role in (RoleEnum.TEAM, RoleEnum.STAFF):
        return not (contest.is_running or contest.is_past)
    raise HTTPException(status_code=403)


def _check_access(actor: UberAdmin | User, contest: Contest) -> None:
    role = actor.role
    if role in (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE, RoleEnum.TEAM, RoleEnum.STAFF):
        if _prestart_access_blocked(actor, contest):
            raise HTTPException(status_code=403)
        return
    raise HTTPException(status_code=403)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


def _find_problem_by_label(problems: list[Problem], label: str) -> Problem | None:
    label_upper = label.upper()
    for p in problems:
        if _label(p.ordinal) == label_upper:
            return p
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse, name="contest_problems")
async def view(
    request: Request,
    ctx: ContestContext = Depends(get_contest_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    access_blocked = _prestart_access_blocked(ctx.actor, ctx.contest)
    if access_blocked:
        return _html(
            templates.TemplateResponse(
                request,
                "contest/problems_list.html",
                {
                    "current_user": ctx.actor,
                    "contest": ctx.contest,
                    "access_blocked": True,
                    "problem_rows": [],
                },
            )
        )

    _check_access(ctx.actor, ctx.contest)
    problems = await get_contest_problems(ctx.session, ctx.contest)
    problem_rows = [
        (p, _label(p.ordinal), len(p.test_cases), sum(1 for tc in p.test_cases if tc.is_sample)) for p in problems
    ]
    return _html(
        templates.TemplateResponse(
            request,
            "contest/problems_list.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "access_blocked": False,
                "problem_rows": problem_rows,
            },
        )
    )


@router.get("/{problem_label}/statement", name="contest_problem_statement")
async def problem_statement(
    request: Request,
    problem_label: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    _check_access(ctx.actor, ctx.contest)
    problems = await get_contest_problems(ctx.session, ctx.contest)
    problem = _find_problem_by_label(problems, problem_label)
    if problem is None:
        raise HTTPException(status_code=404)
    active_path = await anyio.to_thread.run_sync(
        lambda: get_active_statement_path(problem.id, settings.PROBLEM_STATEMENT_DIR)
    )
    if active_path is None:
        return Response(content="Statement not found", status_code=404)
    content = await anyio.to_thread.run_sync(active_path.read_bytes)
    if active_path.suffix == ".md":
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="{problem.title}-statement.md"'},
        )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{problem.title}-statement.pdf"'},
    )


@router.get("/{problem_label}/export", name="contest_problem_export")
async def problem_export(
    request: Request,
    problem_label: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    _check_access(ctx.actor, ctx.contest)
    problems = await get_contest_problems(ctx.session, ctx.contest)
    problem = _find_problem_by_label(problems, problem_label)
    if problem is None:
        raise HTTPException(status_code=404)
    statement_dir = settings.PROBLEM_STATEMENT_DIR
    testcase_dir = settings.PROBLEM_TESTCASE_DIR
    active_stmt = await anyio.to_thread.run_sync(lambda: get_active_statement_path(problem.id, statement_dir))
    if active_stmt is None:
        return Response(content="Statement file is missing — cannot export.", status_code=409)
    zip_bytes = await anyio.to_thread.run_sync(lambda: build_public_export_zip(problem, testcase_dir, statement_dir))
    safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in problem.title)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}-public.zip"'},
    )


@router.get("/{problem_label}", response_class=HTMLResponse, name="contest_problem_detail")
async def problem_detail(
    request: Request,
    problem_label: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    _check_access(ctx.actor, ctx.contest)
    problems = await get_contest_problems(ctx.session, ctx.contest)
    problem = _find_problem_by_label(problems, problem_label)
    if problem is None:
        raise HTTPException(status_code=404)
    label = _label(problem.ordinal)
    public_tcs = sorted(
        (tc for tc in problem.test_cases if tc.is_sample),
        key=lambda t: t.ordinal,
    )
    testcase_dir = settings.PROBLEM_TESTCASE_DIR
    tc_contents: list[tuple[int, str, str, str | None]] = []
    for tc in public_tcs:

        def _read_tc(ordinal: int = tc.ordinal) -> tuple[str, str]:
            return read_testcase_full(problem.id, ordinal, testcase_dir)

        in_text, out_text = await anyio.to_thread.run_sync(_read_tc)
        tc_contents.append((tc.ordinal, in_text, out_text, tc.explanation))

    # Determine statement type for template rendering
    _stmt_dir = settings.PROBLEM_STATEMENT_DIR
    _active_stmt = await anyio.to_thread.run_sync(lambda: get_active_statement_path(problem.id, _stmt_dir))
    has_pdf = _active_stmt is not None and _active_stmt.suffix == ".pdf"
    has_md = _active_stmt is not None and _active_stmt.suffix == ".md"
    md_content = ""
    if has_md and _active_stmt is not None:
        md_content = await anyio.to_thread.run_sync(lambda: _active_stmt.read_text(encoding="utf-8"))

    # Build per-language limits table (override or fallback to problem defaults)
    all_languages = await get_contest_languages(ctx.session, ctx.contest)
    limit_by_lang = {lim.language_id: lim for lim in problem.language_limits}
    language_limits_rows: list[dict[str, object]] = []
    for lang in all_languages:
        lim = limit_by_lang.get(lang.id)
        language_limits_rows.append(
            {
                "name": lang.name,
                "icon": lang.icon,
                "time_limit_ms": lim.time_limit_ms if lim else problem.time_limit_ms,
                "memory_limit_kb": lim.memory_limit_kb if lim else problem.memory_limit_kb,
                "pids_limit": lim.pids_limit if lim else problem.pids_limit,
                "output_limit_in_bytes": lim.output_limit_in_bytes if lim else problem.output_limit_in_bytes,
                "is_override": lim is not None,
            }
        )

    # Quick-submit confirmation modal data, scoped to this single problem.
    lang_map = {lang.id: lang.name for lang in all_languages}
    lang_icon_map = {lang.id: lang.icon for lang in all_languages}
    # File-picker hint: the extension derived from each language's source filename
    # (e.g. "Main.java" -> ".java"), used to filter the quick-submit file input.
    lang_ext_map = {lang.id: PurePosixPath(lang.source_filename).suffix for lang in all_languages}
    problem_labels = {problem.id: f"{label}: {problem.title}"}
    submit_limits: dict[str, dict[str, dict[str, object]]] = {
        problem.id: {
            "default": {
                "time_ms": problem.time_limit_ms,
                "memory_kb": problem.memory_limit_kb,
                "pids": problem.pids_limit,
                "output_bytes": problem.output_limit_in_bytes,
            }
        }
    }
    for lim in problem.language_limits:
        submit_limits[problem.id][lim.language_id] = {
            "time_ms": lim.time_limit_ms,
            "memory_kb": lim.memory_limit_kb,
            "pids": lim.pids_limit,
            "output_bytes": lim.output_limit_in_bytes,
        }

    return _html(
        templates.TemplateResponse(
            request,
            "contest/problem_detail.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "problem": problem,
                "label": label,
                "tc_contents": tc_contents,
                "has_tc_explanation": any(item[3] for item in tc_contents),
                "has_pdf": has_pdf,
                "has_md": has_md,
                "md_content": md_content,
                "language_limits_rows": language_limits_rows,
                "lang_map": lang_map,
                "lang_icon_map": lang_icon_map,
                "lang_ext_map": lang_ext_map,
                "problem_labels": problem_labels,
                "submit_limits": submit_limits,
            },
        )
    )
