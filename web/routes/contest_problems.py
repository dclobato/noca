#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import os
from collections.abc import Sequence
from functools import partial
from http import HTTPStatus
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles

from shared.enumerations import Environment, ProblemValidatorType, RoleEnum
from shared.services.problem_export_cache import ensure_cached_export, export_cache_dir
from shared.services.problem_package import PackageError
from shared.services.problem_package.upload import safe_package_filename, temporary_package_path
from web.config import settings
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest
from web.models.language import Language
from web.models.problem import Problem
from web.models.users import UberAdmin, User
from web.routes.contest_admin_problem_helpers import _label
from web.services.problem_export_rate_limit import web_problem_export_rate_limit
from web.services.problem_list_service import build_problem_cards
from web.services.problem_service import (
    build_problem_export,
    get_active_statement_path,
    get_contest_languages,
    get_contest_problem_refs,
    get_contest_problems,
    get_problem_in_contest,
    load_sample_interactions,
    read_testcase_full,
)
from web.services.scoreboard import ScoreboardService
from web.services.user_read_rate_limit import web_user_read_rate_limit

_scoreboard_service = ScoreboardService()

#: Revalidate before reuse, so the pre-start access gate runs on every view of a
#: statement rather than being skipped for the life of a positive ``max-age``.
STATEMENT_CACHE_CONTROL = "private, no-cache"

#: Answered when production has no export cache configured. Web refuses to start
#: in that state, so this is the guard for a configuration that changed under a
#: running process rather than the expected path.
UNCACHED_IN_PRODUCTION_DETAIL = "Problem export cache is not configured."

#: Borrowed purely for its conditional-request handling; it is never mounted, and
#: ``file_response`` does not touch instance state.
_CONDITIONAL_FILES = StaticFiles()

router = APIRouter(
    prefix="/c/{slug}/problems", tags=["contest_problems"], dependencies=[Depends(web_user_read_rate_limit)]
)


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


class _HasOrdinal(Protocol):
    """The one attribute label resolution needs."""

    @property
    def ordinal(self) -> int: ...


def _find_problem_by_label[T: _HasOrdinal](problems: Sequence[T], label: str) -> T | None:
    """Resolve a display label to its problem, over full rows or light references."""
    label_upper = label.upper()
    for p in problems:
        if _label(p.ordinal) == label_upper:
            return p
    return None


async def _build_public_export(ctx: ContestContext, problem_id: str, destination: Path) -> None:
    """Write one problem's ``public`` package to ``destination``.

    Loads the eager graph the builder needs only at this point: a cache hit never
    reaches here, so the heavy query is paid on a rebuild rather than per request.

    Raises:
        PackageError: If a stored file the package needs is missing.
    """
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise PackageError("Cannot export: the problem no longer exists.")
    await anyio.to_thread.run_sync(
        partial(
            build_problem_export,
            problem,
            settings.PROBLEM_TESTCASE_DIR,
            settings.PROBLEM_STATEMENT_DIR,
            destination,
            profile="public",
        )
    )


async def _load_problem_view_data(ctx: ContestContext, problem: Problem) -> dict[str, object]:
    """Load statement type, samples, interactions, and per-language limits for a problem.

    Shared by the problem detail page and the print-friendly view so both render the
    same statement/samples/limits from a single source of truth.

    Args:
        ctx: Active contest context (session and contest).
        problem: The problem whose view data is being assembled.

    Returns:
        A mapping with statement flags (``has_pdf``/``has_md``/``md_content``), the
        public sample test cases (``tc_contents``), sample interactions, the custom
        validator flag, the per-language limits table, and the active language list.
    """
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

    # An interactive problem's public examples are conversations, not test cases.
    sample_interactions = await load_sample_interactions(ctx.session, problem.id)

    # Determine statement type for template rendering.
    stmt_dir = settings.PROBLEM_STATEMENT_DIR
    active_stmt = await anyio.to_thread.run_sync(lambda: get_active_statement_path(problem.id, stmt_dir))
    has_pdf = active_stmt is not None and active_stmt.suffix == ".pdf"
    has_md = active_stmt is not None and active_stmt.suffix == ".md"
    md_content = ""
    if has_md and active_stmt is not None:
        md_content = await anyio.to_thread.run_sync(lambda: active_stmt.read_text(encoding="utf-8"))

    # Build per-language limits table (override or fallback to problem defaults).
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

    return {
        "tc_contents": tc_contents,
        "sample_interactions": sample_interactions,
        "has_custom_validator": problem.validator_type is ProblemValidatorType.INTERACTIVE,
        # Both sample test cases and sample interactions carry Markdown explanations,
        # and an interactive problem has only the latter (all its cases are secret).
        "has_explanation_markdown": any(item[3] for item in tc_contents)
        or any(si.explanation for si in sample_interactions),
        "has_pdf": has_pdf,
        "has_md": has_md,
        "md_content": md_content,
        "language_limits_rows": language_limits_rows,
        "all_languages": all_languages,
    }


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
    is_admin_viewer = isinstance(ctx.actor, UberAdmin) or (
        hasattr(ctx.actor, "role") and ctx.actor.role in (RoleEnum.ADMIN, RoleEnum.JUDGE)
    )
    valkey = request.app.state.valkey_runtime
    snapshot = await _scoreboard_service.get_cached_or_compute(
        ctx.contest, "admin" if is_admin_viewer else "public", ctx.session, valkey
    )
    problem_rows = build_problem_cards(problems, snapshot, ctx.actor)
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
    """Serve a problem statement, revalidated rather than rebuilt.

    Every team opens its statements repeatedly during a contest, so the response
    is conditional: a browser that already holds the file revalidates and gets a
    bodyless ``304`` costing one ``stat()``.

    The directive is ``private, no-cache`` and not a positive ``max-age``. A
    positive age lets the browser reuse the response *without contacting the
    server*, and therefore without re-running :func:`_check_access` -- which is
    what withholds statements from teams until the contest starts. ``no-cache``
    keeps the gate on every reuse while keeping that reuse cheap.
    """
    _check_access(ctx.actor, ctx.contest)
    refs = await get_contest_problem_refs(ctx.session, ctx.contest)
    problem = _find_problem_by_label(refs, problem_label)
    if problem is None:
        raise HTTPException(status_code=404)
    active_path = await anyio.to_thread.run_sync(
        lambda: get_active_statement_path(problem.id, settings.PROBLEM_STATEMENT_DIR)
    )
    if active_path is None:
        return Response(content="Statement not found", status_code=404)
    try:
        stat_result = await anyio.to_thread.run_sync(os.stat, active_path)
    except OSError:
        # The row says there is a statement but the file went away underneath us.
        return Response(content="Statement not found", status_code=404)

    is_markdown = active_path.suffix == ".md"
    suffix = "-statement.md" if is_markdown else "-statement.pdf"
    # `StaticFiles.file_response` evaluates `If-None-Match` / `If-Modified-Since`
    # and returns Starlette's bodyless `NotModifiedResponse` on a match; a plain
    # `FileResponse` emits the validators but never reads them, so it can never
    # answer `304`. It also streams a `200` instead of reading the whole file
    # into memory. The helper does not touch `self`, so one module-level instance
    # serves every statement path.
    response = _CONDITIONAL_FILES.file_response(active_path, stat_result, request.scope)
    # Always: a `304` has to repeat the caching directives, or the next reuse is
    # governed by whatever the client inferred instead.
    response.headers["cache-control"] = STATEMENT_CACHE_CONTROL
    if response.status_code == HTTPStatus.NOT_MODIFIED:
        return response

    response.headers["content-type"] = "text/markdown; charset=utf-8" if is_markdown else "application/pdf"
    # `safe_package_filename` emits only `[A-Za-z0-9-_]`, so the quoted header
    # cannot be broken by a title carrying quotes or non-ASCII characters.
    filename = safe_package_filename(problem.title, suffix=suffix)
    response.headers["content-disposition"] = f'inline; filename="{filename}"'
    return response


@router.get(
    "/{problem_label}/export",
    name="contest_problem_export",
    dependencies=[Depends(web_problem_export_rate_limit)],
)
async def problem_export(
    request: Request,
    problem_label: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    """Serve a problem's contestant-facing package, from cache when configured.

    Building this package is expensive and the route is reachable by every team,
    so a configured cache is what makes it safe to expose during a contest. In
    production that cache is mandatory -- Web refuses to start without it -- and
    the ``503`` below is the per-request guard for the state a misconfiguration
    could still reach.
    """
    cache_dir = settings.PUBLIC_PROBLEM_PACK_PATH
    if cache_dir is None and settings.ENVIRONMENT == Environment.PRODUCTION:
        raise HTTPException(status_code=503, detail=UNCACHED_IN_PRODUCTION_DETAIL)

    _check_access(ctx.actor, ctx.contest)
    refs = await get_contest_problem_refs(ctx.session, ctx.contest)
    problem_ref = _find_problem_by_label(refs, problem_label)
    if problem_ref is None:
        raise HTTPException(status_code=404)
    filename = safe_package_filename(problem_ref.title, suffix="-public.zip")

    if cache_dir is not None:
        try:
            archive_path = await ensure_cached_export(
                export_cache_dir(cache_dir),
                problem_ref.id,
                problem_ref.public_export_generation,
                lambda destination: _build_public_export(ctx, problem_ref.id, destination),
            )
        except PackageError as exc:
            return Response(content=str(exc), status_code=409)
        # The archive belongs to the cache, so the response must not delete it.
        return FileResponse(archive_path, media_type="application/zip", filename=filename)

    with temporary_package_path() as destination:
        try:
            await _build_public_export(ctx, problem_ref.id, destination)
        except PackageError as exc:
            destination.unlink(missing_ok=True)
            return Response(content=str(exc), status_code=409)
    return FileResponse(
        destination,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(destination.unlink, missing_ok=True),
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
    # Prev/next follow the same ordinal ordering that assigns the labels, so the
    # buttons walk the contest exactly as the scoreboard and the list do.
    problem_index = problems.index(problem)
    prev_problem = problems[problem_index - 1] if problem_index > 0 else None
    next_problem = problems[problem_index + 1] if problem_index + 1 < len(problems) else None
    view_data = await _load_problem_view_data(ctx, problem)
    tc_contents = cast("list[tuple[int, str, str, str | None]]", view_data["tc_contents"])
    all_languages = cast("list[Language]", view_data["all_languages"])

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
                "prev_label": _label(prev_problem.ordinal) if prev_problem else None,
                "next_label": _label(next_problem.ordinal) if next_problem else None,
                "tc_contents": tc_contents,
                "sample_interactions": view_data["sample_interactions"],
                "has_custom_validator": view_data["has_custom_validator"],
                "has_explanation_markdown": view_data["has_explanation_markdown"],
                "has_pdf": view_data["has_pdf"],
                "has_md": view_data["has_md"],
                "md_content": view_data["md_content"],
                "language_limits_rows": view_data["language_limits_rows"],
                "lang_map": lang_map,
                "lang_icon_map": lang_icon_map,
                "lang_ext_map": lang_ext_map,
                "problem_labels": problem_labels,
                "submit_limits": submit_limits,
            },
        )
    )


@router.get("/{problem_label}/print", response_class=HTMLResponse, name="contest_problem_print")
async def problem_print(
    request: Request,
    problem_label: str,
    ctx: ContestContext = Depends(get_contest_context),
) -> HTMLResponse:
    """Render a standalone, print-friendly view of a problem (statement, samples, limits)."""
    templates = request.app.state.templates
    _check_access(ctx.actor, ctx.contest)
    problems = await get_contest_problems(ctx.session, ctx.contest)
    problem = _find_problem_by_label(problems, problem_label)
    if problem is None:
        raise HTTPException(status_code=404)
    label = _label(problem.ordinal)
    view_data = await _load_problem_view_data(ctx, problem)
    return _html(
        templates.TemplateResponse(
            request,
            "contest/problem_print.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "problem": problem,
                "label": label,
                "tc_contents": view_data["tc_contents"],
                "sample_interactions": view_data["sample_interactions"],
                "has_custom_validator": view_data["has_custom_validator"],
                "has_explanation_markdown": view_data["has_explanation_markdown"],
                "has_pdf": view_data["has_pdf"],
                "has_md": view_data["has_md"],
                "md_content": view_data["md_content"],
                "language_limits_rows": view_data["language_limits_rows"],
            },
        )
    )
