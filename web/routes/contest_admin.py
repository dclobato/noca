#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from shared.services.admin_audit import record_admin_action
from web.config import settings as _settings
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.routes import contest_admin_export as _contest_admin_export
from web.routes import contest_admin_metadata as _contest_admin_metadata
from web.routes import contest_admin_reports as _contest_admin_reports
from web.routes.contest_admin_helpers import (
    _build_contest_admin_counters,
    _end_contest_now,
    _html,
    _is_actor_password_valid,
)
from web.services.contest_service import ensure_contest_has_sites
from web.services.judging_service import (
    ChiefJudgeRemovalBlockedError,
    remove_chief_judge,
    set_chief_judge,
)
from web.services.problem_set_cache import discard_cached_archive
from web.services.scoreboard import ScoreboardService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/c/{slug}/admin", tags=["contest_admin"])

_score_service = ScoreboardService()

edit_metadata = _contest_admin_metadata.edit_metadata
edit_metadata_submit = _contest_admin_metadata.edit_metadata_submit
manage_users = _contest_admin_reports.manage_users
import_export = _contest_admin_export.import_export
export_animeitor = _contest_admin_export.export_animeitor
export_contest_timeline = _contest_admin_export.export_contest_timeline
users_per_site_report = _contest_admin_export.users_per_site_report

__all__ = [
    "router",
    "edit_metadata",
    "edit_metadata_submit",
    "manage_users",
    "import_export",
    "export_animeitor",
    "export_contest_timeline",
    "users_per_site_report",
]


@router.get("/", response_class=HTMLResponse)
async def view(request: Request, ctx: ContestAdminContext = Depends(get_contest_admin_context)) -> HTMLResponse:
    templates = request.app.state.templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/dashboard.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
            },
        )
    )


@router.get("/counters", response_class=HTMLResponse, name="contest_admin_counters")
async def counters(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    """Render contest admin counters page."""
    templates = request.app.state.templates
    counters_data = await _build_contest_admin_counters(request, ctx)
    return _html(
        templates.TemplateResponse(
            request,
            "admin/counters.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "counters": counters_data,
            },
        )
    )


@router.post("/start-now", response_model=None, name="contest_start_now")
async def start_contest_now(
    request: Request,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    password: str = Form(""),
) -> Response:
    if not ctx.contest.is_running and not ctx.contest.is_past:
        if not _is_actor_password_valid(ctx.actor, password):
            flash("Password confirmation is incorrect.", FlashCategory.DANGER)
            return RedirectResponse(url=f"/c/{ctx.contest.login_slug}", status_code=303)
        try:
            await ensure_contest_has_sites(ctx.session, ctx.contest)
        except ValueError as exc:
            flash(str(exc), FlashCategory.DANGER)
        else:
            ctx.contest.start_time = datetime.now(UTC)
            await record_admin_action(
                ctx.session,
                request,
                module="web",
                actor_user_id=ctx.actor.id,
                actor_label=ctx.actor.username,
                action="contest_start_now",
                target_type="contest",
                target_id=ctx.contest.id,
                detail=f"slug={ctx.contest.login_slug}",
            )
            await ctx.session.commit()
    return RedirectResponse(url=f"/c/{ctx.contest.login_slug}", status_code=303)


@router.post("/end-now", response_model=None, name="contest_end_now")
async def end_contest_now(
    request: Request,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    password: str = Form(""),
) -> Response:
    if ctx.contest.is_running:
        if not _is_actor_password_valid(ctx.actor, password):
            flash("Password confirmation is incorrect.", FlashCategory.DANGER)
            return RedirectResponse(url=f"/c/{ctx.contest.login_slug}", status_code=303)
        _end_contest_now(ctx.contest, now=datetime.now(UTC))
        await record_admin_action(
            ctx.session,
            request,
            module="web",
            actor_user_id=ctx.actor.id,
            actor_label=ctx.actor.username,
            action="contest_end_now",
            target_type="contest",
            target_id=ctx.contest.id,
            detail=f"slug={ctx.contest.login_slug}",
        )
        await ctx.session.commit()
        flash("Contest duration adjusted. Contest will end shortly.", FlashCategory.SUCCESS)
    return RedirectResponse(url=f"/c/{ctx.contest.login_slug}", status_code=303)


@router.post("/chief-judge", response_model=None, name="contest_admin_set_chief_judge")
async def set_chief_judge_route(
    request: Request,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    action: str = Form("assign"),
    judge_id: str = Form(""),
) -> Response:
    redirect_url = str(request.url_for("manage_users", slug=ctx.contest.login_slug))

    try:
        if action == "remove":
            await remove_chief_judge(ctx.session, ctx.contest, ctx.actor)
            success_message = "Chief judge removed."
        else:
            await set_chief_judge(ctx.session, ctx.contest, judge_id.strip() or None, ctx.actor)
            success_message = "Chief judge updated." if judge_id.strip() else "Chief judge cleared."
    except ChiefJudgeRemovalBlockedError:
        flash(
            "The current chief judge cannot be removed because they have already executed a verdict override.",
            FlashCategory.DANGER,
        )
        return RedirectResponse(url=redirect_url, status_code=303)
    except HTTPException as exc:
        if exc.status_code == 400:
            flash(str(exc.detail), FlashCategory.DANGER)
            return RedirectResponse(url=redirect_url, status_code=303)
        if exc.status_code == 403:
            flash("Only the contest owner or an uberadmin can assign the chief judge.", FlashCategory.DANGER)
            return RedirectResponse(url=redirect_url, status_code=303)
        raise

    await ctx.session.commit()
    flash(success_message, FlashCategory.SUCCESS)
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/release-scoreboard", response_model=None, name="contest_admin_release_scoreboard")
async def release_scoreboard(
    request: Request,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> Response:
    """Release the final scoreboard for an ended contest.

    Sets ``Contest.release_scoreboard_after_end`` to True, pre-warms the permanent
    Valkey cache with all results revealed, and commits. After this, every scoreboard
    request for the contest returns the final standings with no freeze applied.

    The release is audited in the same transaction as the flag it writes, so an
    irreversible reveal cannot land without a named trail.
    """
    redirect_url = f"/c/{ctx.contest.login_slug}/admin/"

    if not ctx.contest.is_past:
        flash("Contest has not ended yet.", FlashCategory.DANGER)
        return RedirectResponse(url=redirect_url, status_code=303)

    if ctx.contest.release_scoreboard_after_end:
        flash("Final scoreboard is already released.", FlashCategory.WARNING)
        return RedirectResponse(url=redirect_url, status_code=303)

    ctx.contest.release_scoreboard_after_end = True
    await record_admin_action(
        ctx.session,
        request,
        module="web",
        actor_user_id=ctx.actor.id,
        actor_label=ctx.actor.username,
        action="contest_scoreboard_release",
        target_type="contest",
        target_id=ctx.contest.id,
        detail=f"slug={ctx.contest.login_slug}",
        # Warning level, like the problem-set publication beside it: this reveals
        # every result the freeze held back, to everyone, and there is no route
        # that undoes it. There is no revoke half to record at info level.
        severity="warning",
    )
    await _score_service.get_or_compute_final(ctx.contest, ctx.session, request.app.state.valkey_runtime)
    await ctx.session.commit()

    flash("Final scoreboard released successfully.", FlashCategory.SUCCESS)
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/release-problem-set", response_model=None, name="contest_admin_release_problem_set")
async def release_problem_set(
    request: Request,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    release: str = Form(...),
) -> Response:
    """Publish or withdraw the contest's public problem-set download.

    ``release`` is a strict ``"yes"``/``"no"`` string rather than a bool so
    FastAPI cannot coerce ``1``/``on`` into a choice between publishing every
    secret test case and withdrawing it, and so a resubmitted form is idempotent
    instead of flipping the state back.

    The guard is asymmetric on purpose. Publishing requires the contest to be
    over -- arming a future publication is the metadata form's job, so this
    control never releases something that is not ready. Withdrawing is always
    allowed, because it serves both as Revoke after the end and as Cancel on a
    contest that was armed and has not ended yet.

    Both directions are audited in the same transaction as the flag they write,
    so a publish or withdraw cannot land without a named trail.
    """
    redirect_url = f"/c/{ctx.contest.login_slug}/admin/"

    if release not in {"yes", "no"}:
        flash("Invalid problem-set release request.", FlashCategory.DANGER)
        return RedirectResponse(url=redirect_url, status_code=303)

    should_release = release == "yes"

    if should_release and not ctx.contest.is_past:
        flash(
            "Contest has not ended yet. Arm the release from Edit metadata to publish automatically at the end.",
            FlashCategory.DANGER,
        )
        return RedirectResponse(url=redirect_url, status_code=303)

    if ctx.contest.release_problem_set_after_end == should_release:
        flash(
            "Problem set is already released." if should_release else "Problem set is already withheld.",
            FlashCategory.WARNING,
        )
        return RedirectResponse(url=redirect_url, status_code=303)

    ctx.contest.release_problem_set_after_end = should_release
    await record_admin_action(
        ctx.session,
        request,
        module="web",
        actor_user_id=ctx.actor.id,
        actor_label=ctx.actor.username,
        action="contest_problem_set_release" if should_release else "contest_problem_set_revoke",
        target_type="contest",
        target_id=ctx.contest.id,
        detail=f"slug={ctx.contest.login_slug}",
        # Publishing exposes secret material to anonymous callers, which is what
        # `uberadmin_contest_backup` already records at warning level when an
        # export carries password hashes or user media. Withdrawing only reduces
        # exposure, so it stays at the default info level.
        severity="warning" if should_release else "info",
    )
    await ctx.session.commit()

    if not should_release:
        # Best effort, and only after the commit: the route gate already blocks
        # the download, so a cache that outlives the revoke is not a disclosure.
        # Dropping it keeps a later re-release from serving an archive built
        # before the problems were edited.
        cache_dir = _settings.PUBLIC_PROBLEM_PACK_PATH
        if cache_dir is not None:
            try:
                await discard_cached_archive(cache_dir, ctx.contest)
            except OSError:
                logger.warning(
                    "Could not drop the cached problem-set archive for %s",
                    ctx.contest.login_slug,
                    exc_info=True,
                )

    flash(
        "Problem set released successfully." if should_release else "Problem set is no longer public.",
        FlashCategory.SUCCESS,
    )
    return RedirectResponse(url=redirect_url, status_code=303)
