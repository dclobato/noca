#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.routes.contest_clarifications_helpers import _ADMIN_ONLY, _JUDGE_ONLY, _build_problem_map, _html
from web.services.clarification_service import (
    get_clarification,
    release_clarification,
    toggle_hidden_clarification,
)

router = APIRouter(prefix="/c/{slug}/clarifications", tags=["contest_clarifications"])


@router.get("/hide", response_class=HTMLResponse, name="contest_clarifications_hide")
async def hide_form(
    request: Request,
    flash: FlashDep,
    ctx: ContestContext = Depends(get_contest_context),
    id: str = Query(""),
) -> Response:
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, _JUDGE_ONLY)
    slug = ctx.contest.login_slug

    clari = await get_clarification(ctx.session, ctx.contest, id)
    if clari is None:
        flash("Clarification not found.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)

    problem_map = await _build_problem_map(ctx.session, ctx.contest)
    return _html(
        templates.TemplateResponse(
            request,
            "admin/clarifications/hide.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "clarification": clari,
                "problem_map": problem_map,
            },
        )
    )


@router.get("/togglehide", response_class=HTMLResponse, name="contest_clarifications_togglehide")
async def togglehide_form(
    request: Request,
    flash: FlashDep,
    ctx: ContestContext = Depends(get_contest_context),
    id: str = Query(""),
) -> Response:
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, _ADMIN_ONLY)
    slug = ctx.contest.login_slug

    clari = await get_clarification(ctx.session, ctx.contest, id)
    if clari is None:
        flash("Clarification not found.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)

    problem_map = await _build_problem_map(ctx.session, ctx.contest)
    return _html(
        templates.TemplateResponse(
            request,
            "admin/clarifications/togglehide.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "clarification": clari,
                "problem_map": problem_map,
            },
        )
    )


@router.post(
    "/togglehide", response_class=HTMLResponse, response_model=None, name="contest_clarifications_togglehide_submit"
)
async def togglehide_submit(
    request: Request,
    flash: FlashDep,
    ctx: ContestContext = Depends(get_contest_context),
    clarification_id: str = Form(""),
    action: str = Form("cancel"),
) -> Response:
    ensure_allowed_role(ctx.actor, _ADMIN_ONLY)
    slug = ctx.contest.login_slug

    if action == "cancel":
        return RedirectResponse(url=f"/c/{slug}/clarifications/#{clarification_id}", status_code=303)

    clari = await get_clarification(ctx.session, ctx.contest, clarification_id)
    if clari is None:
        flash("Clarification not found.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)

    was_hidden = clari.hidden
    await toggle_hidden_clarification(ctx.session, ctx.actor, clari)
    await ctx.session.commit()

    flash(
        "Clarification is now visible to teams." if was_hidden else "Clarification hidden from teams.",
        FlashCategory.SUCCESS if was_hidden else FlashCategory.WARNING,
    )
    return RedirectResponse(url=f"/c/{slug}/clarifications/#{clarification_id}", status_code=303)


@router.post("/hide", response_class=HTMLResponse, response_model=None, name="contest_clarifications_hide_submit")
async def hide_submit(
    request: Request,
    flash: FlashDep,
    ctx: ContestContext = Depends(get_contest_context),
    clarification_id: str = Form(""),
    action: str = Form("cancel"),
) -> Response:
    ensure_allowed_role(ctx.actor, _JUDGE_ONLY)
    slug = ctx.contest.login_slug

    if action == "cancel":
        return RedirectResponse(url=f"/c/{slug}/clarifications/#{clarification_id}", status_code=303)

    clari = await get_clarification(ctx.session, ctx.contest, clarification_id)
    if clari is None:
        flash("Clarification not found.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)

    await toggle_hidden_clarification(ctx.session, ctx.actor, clari)
    await ctx.session.commit()
    flash("Clarification hidden from teams.", FlashCategory.WARNING)
    return RedirectResponse(url=f"/c/{slug}/clarifications/#{clarification_id}", status_code=303)


@router.post(
    "/releaselock", response_class=HTMLResponse, response_model=None, name="contest_clarifications_releaselock"
)
async def releaselock_submit(
    request: Request,
    flash: FlashDep,
    ctx: ContestContext = Depends(get_contest_context),
    clarification_id: str = Form(""),
) -> Response:
    ensure_allowed_role(ctx.actor, _ADMIN_ONLY)
    slug = ctx.contest.login_slug

    clari = await get_clarification(ctx.session, ctx.contest, clarification_id)
    if clari is None:
        flash("Clarification not found.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)

    if clari.answered_at is not None:
        flash("This clarification has already been answered.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/#{clarification_id}", status_code=303)

    if not request.app.state.valkey_runtime.is_available:
        flash("Lock service unavailable. Possible double work/rework.", FlashCategory.WARNING)
        return RedirectResponse(url=f"/c/{slug}/clarifications/#{clarification_id}", status_code=303)

    await release_clarification(
        ctx.session,
        ctx.contest,
        ctx.actor,
        clari,
        request.app.state.valkey_runtime,
    )
    await ctx.session.commit()

    flash("Clarification lock released.", FlashCategory.INFO)
    return RedirectResponse(url=f"/c/{slug}/clarifications/#{clarification_id}", status_code=303)
