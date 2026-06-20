#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_flash import FlashCategory, FlashDep

from shared.queue_schema import JudgeJob
from shared.services.scoreboard_cache import invalidate_scoreboard_cache
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.routes.contest_admin_problem_helpers import _html, _is_edit_allowed, _is_limits_edit_allowed, _redirect
from web.routes.contest_admin_problem_limits_helpers import _build_profiling_limits_context, _limit_batch_groups
from web.services.judging_service import queue_limit_change_batch_rejudges
from web.services.problem_service import (
    apply_fallback_limits,
    changed_effective_limits,
    create_problem_limit_change_batch,
    create_profiling_run,
    enqueue_profiling_job,
    get_active_profiling_run_for_problem,
    get_contest_languages,
    get_language_limits_map,
    get_problem_in_contest,
    get_problem_limit_change_batch,
    problem_fallback_limits,
)
from web.services.valkey_service import enqueue_job

router = APIRouter(prefix="/c/{slug}/admin/problems", tags=["contest_admin_problems"])


@router.post("/{problem_id}/profiling", name="enqueue_problem_profiling")
async def enqueue_problem_profiling(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    language_id: str = Form(""),
    safety_factor: str = Form("1.5"),
    reference_file: UploadFile = File(None),
) -> RedirectResponse:
    redirect_url = (
        str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id)) + "?tab=limits"
    )
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(redirect_url)
    if not _is_edit_allowed(ctx.contest):
        flash("Contest is not editable.", FlashCategory.DANGER)
        return _redirect(redirect_url)

    languages = await get_contest_languages(ctx.session, ctx.contest)
    if language_id not in {language.id for language in languages}:
        flash("Invalid language for this contest.", FlashCategory.DANGER)
        return _redirect(redirect_url)
    active_profiling_run = await get_active_profiling_run_for_problem(ctx.session, problem)
    if active_profiling_run is not None:
        flash(
            f"Auto-Limit is already running for {active_profiling_run.language.name}. Wait for it to finish.",
            FlashCategory.DANGER,
        )
        return _redirect(redirect_url)

    try:
        safety_factor_value = float(safety_factor)
        if safety_factor_value <= 1.0:
            raise ValueError
    except ValueError:
        flash("Safety factor must be greater than 1.0.", FlashCategory.DANGER)
        return _redirect(redirect_url)

    if reference_file is None or not reference_file.filename:
        flash("Reference implementation file is required.", FlashCategory.DANGER)
        return _redirect(redirect_url)

    source_code = (await reference_file.read()).decode("utf-8", errors="replace")
    profiling_run = await create_profiling_run(
        ctx.session,
        problem,
        language_id=language_id,
        source_code=source_code,
        safety_factor=safety_factor_value,
        triggered_by_user_id=ctx.actor.id,
    )
    await ctx.session.commit()
    await enqueue_profiling_job(request.app.state.valkey_runtime, profiling_run, contest_id=ctx.contest.id)
    flash("Profiling run queued.", FlashCategory.SUCCESS)
    return _redirect(redirect_url)


@router.get(
    "/{problem_id}/profiling-status",
    response_class=HTMLResponse,
    name="problem_profiling_status_partial",
)
async def problem_profiling_status_partial(
    request: Request,
    problem_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")
    form_data = {
        "title": problem.title,
        "color": problem.color or "#000000",
        "author": problem.author or "",
        "notes": problem.notes or "",
        "time_limit_ms": problem.time_limit_ms,
        "memory_limit_kb": problem.memory_limit_kb,
        "pids_limit": problem.pids_limit,
        "output_limit_in_bytes": problem.output_limit_in_bytes or "",
    }
    context = await _build_profiling_limits_context(request, ctx, problem, form_data)
    return _html(templates.TemplateResponse(request, "admin/problems/profiling_limits.html", context))


@router.post("/{problem_id}/fallback-limits", name="apply_problem_fallback_limits")
async def apply_problem_fallback_limits_route(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> RedirectResponse:
    redirect_url = (
        str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id)) + "?tab=limits"
    )
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(redirect_url)
    if not _is_limits_edit_allowed(ctx.contest):
        flash("Problem limits cannot be edited right now.", FlashCategory.DANGER)
        return _redirect(redirect_url)

    languages = await get_contest_languages(ctx.session, ctx.contest)
    existing_limits = await get_language_limits_map(ctx.session, problem)
    before_fallback = problem_fallback_limits(problem)
    updated = await apply_fallback_limits(ctx.session, problem)
    if not updated:
        flash("No per-language limits exist for this problem.", FlashCategory.DANGER)
        return _redirect(redirect_url)

    batch = await create_problem_limit_change_batch(
        ctx.session,
        ctx.contest,
        problem,
        ctx.actor,
        changed_effective_limits(
            problem,
            languages,
            before_overrides=existing_limits,
            after_overrides=existing_limits,
            before_fallback=before_fallback,
            after_fallback=problem_fallback_limits(problem),
        ),
    )
    await ctx.session.commit()
    if batch is not None:
        flash("Fallback limits updated. Review the affected submissions batch.", FlashCategory.SUCCESS)
        return _redirect(
            str(
                request.url_for(
                    "problem_limit_change_batch_review",
                    slug=ctx.contest.login_slug,
                    problem_id=problem.id,
                    batch_id=batch.id,
                )
            )
        )
    flash(
        "Fallback limits updated from per-language maxima. No affected submissions were found.",
        FlashCategory.SUCCESS,
    )
    return _redirect(redirect_url)


@router.get(
    "/{problem_id}/limit-change-batches/{batch_id}",
    response_class=HTMLResponse,
    name="problem_limit_change_batch_review",
)
async def problem_limit_change_batch_review(
    request: Request,
    problem_id: str,
    batch_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")

    batch = await get_problem_limit_change_batch(ctx.session, ctx.contest, problem_id, batch_id)
    if batch is None:
        raise Exception("Limit change batch not found")

    language_groups = _limit_batch_groups(batch)
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/limit_change_batch.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "problem": problem,
                "batch": batch,
                "language_groups": language_groups,
                "total_pending": sum(group["pending_count"] for group in language_groups),
            },
        )
    )


async def _queue_limit_batch_rejudges(
    request: Request,
    problem_id: str,
    batch_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext,
    *,
    language_id: str | None = None,
) -> RedirectResponse:
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(str(request.url_for("manage_problems", slug=ctx.contest.login_slug)))

    batch = await get_problem_limit_change_batch(ctx.session, ctx.contest, problem_id, batch_id)
    if batch is None:
        flash("Limit change batch not found.", FlashCategory.DANGER)
        return _redirect(str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id)))

    new_judgments = await queue_limit_change_batch_rejudges(
        ctx.session,
        batch,
        ctx.contest,
        ctx.actor,
        request.app.state.valkey_runtime,
        language_id=language_id,
    )
    await ctx.session.commit()
    for judgment in new_judgments:
        await enqueue_job(
            request.app.state.valkey_runtime,
            JudgeJob(
                judgment_id=judgment.id,
                contest_id=str(ctx.contest.id),
                submission_id=str(judgment.submission_id),
                is_rejudge=True,
                requeue_count=0,
            ),
            priority=True,
        )
    if new_judgments:
        await invalidate_scoreboard_cache(request.app.state.valkey_runtime, str(ctx.contest.id))
        flash(f"Queued {len(new_judgments)} submissions for rejudging.", FlashCategory.SUCCESS)
    else:
        flash("No pending submissions were eligible for rejudging.", FlashCategory.WARNING)
    return _redirect(
        str(
            request.url_for(
                "problem_limit_change_batch_review",
                slug=ctx.contest.login_slug,
                problem_id=problem_id,
                batch_id=batch_id,
            )
        )
    )


@router.post(
    "/{problem_id}/limit-change-batches/{batch_id}/rejudge-all",
    name="problem_limit_change_batch_rejudge_all",
)
async def problem_limit_change_batch_rejudge_all(
    request: Request,
    problem_id: str,
    batch_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> RedirectResponse:
    return await _queue_limit_batch_rejudges(request, problem_id, batch_id, flash, ctx)


@router.post(
    "/{problem_id}/limit-change-batches/{batch_id}/languages/{language_id}/rejudge",
    name="problem_limit_change_batch_rejudge_language",
)
async def problem_limit_change_batch_rejudge_language(
    request: Request,
    problem_id: str,
    batch_id: str,
    language_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> RedirectResponse:
    return await _queue_limit_batch_rejudges(request, problem_id, batch_id, flash, ctx, language_id=language_id)
