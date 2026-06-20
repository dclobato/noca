#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from shared.services.lock_service import get_lock
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.models.users import User
from web.routes.contest_clarifications_helpers import _JUDGE_ONLY, _build_problem_map, _html
from web.services.clarification_service import (
    ClarificationAcquisitionTimeoutError,
    ClarificationAlreadyAcquiredError,
    ClarificationAlreadyAnsweredError,
    ClarificationHiddenError,
    ClarificationLockUnavailableError,
    ClarificationNotAcquiredByActorError,
    ContestNotRunningError,
    acquire_clarification,
    answer_clarification,
    get_clarification,
    release_clarification,
)

router = APIRouter(prefix="/c/{slug}/clarifications", tags=["contest_clarifications"])


@router.post("/acquire", response_class=HTMLResponse, response_model=None, name="contest_clarifications_acquire")
async def acquire(
    request: Request,
    flash: FlashDep,
    ctx: ContestContext = Depends(get_contest_context),
    clarification_id: str = Form(""),
) -> Response:
    ensure_allowed_role(ctx.actor, _JUDGE_ONLY)
    assert isinstance(ctx.actor, User)
    slug = ctx.contest.login_slug

    clari = await get_clarification(ctx.session, ctx.contest, clarification_id)
    if clari is None:
        flash("Clarification not found.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)

    if not request.app.state.valkey_runtime.is_available:
        flash("Lock service unavailable. Possible double work/rework.", FlashCategory.WARNING)
        return RedirectResponse(url=f"/c/{slug}/clarifications/answer?id={clarification_id}", status_code=303)

    current_lock = await get_lock(
        request.app.state.valkey_runtime,
        kind="clarification",
        contest_id=ctx.contest.id,
        resource_id=clarification_id,
    )
    if current_lock is not None and current_lock.holder_id == ctx.actor.id:
        return RedirectResponse(url=f"/c/{slug}/clarifications/answer?id={clarification_id}", status_code=303)

    try:
        await acquire_clarification(
            ctx.session,
            ctx.contest,
            ctx.actor,
            clari,
            request.app.state.valkey_runtime,
        )
        await ctx.session.commit()
    except ClarificationAlreadyAcquiredError:
        flash("This clarification is already being worked on by another judge.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/#{clarification_id}", status_code=303)
    except ClarificationAlreadyAnsweredError:
        flash("This clarification has already been answered.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/#{clarification_id}", status_code=303)
    except ClarificationHiddenError:
        flash("This clarification is hidden and cannot be acquired.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/#{clarification_id}", status_code=303)
    except ContestNotRunningError:
        flash("The contest is not currently running.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/#{clarification_id}", status_code=303)
    except ClarificationLockUnavailableError:
        flash("Lock service unavailable. Possible double work/rework.", FlashCategory.WARNING)
        return RedirectResponse(url=f"/c/{slug}/clarifications/answer?id={clarification_id}", status_code=303)

    return RedirectResponse(url=f"/c/{slug}/clarifications/answer?id={clarification_id}", status_code=303)


@router.get("/answer", response_class=HTMLResponse, name="contest_clarifications_answer")
async def answer_form(
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

    if request.app.state.valkey_runtime.is_available:
        current_lock = await get_lock(
            request.app.state.valkey_runtime,
            kind="clarification",
            contest_id=ctx.contest.id,
            resource_id=id,
        )
        if current_lock is None or current_lock.holder_id != ctx.actor.id:
            flash("You do not hold the lock on this clarification.", FlashCategory.DANGER)
            return RedirectResponse(url=f"/c/{slug}/clarifications/#{id}", status_code=303)

    problem_map = await _build_problem_map(ctx.session, ctx.contest)
    return _html(
        templates.TemplateResponse(
            request,
            "admin/clarifications/answer.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "clarification": clari,
                "problem_map": problem_map,
                "errors": [],
                "form_data": {},
                "lock_service_available": request.app.state.valkey_runtime.is_available,
            },
        )
    )


@router.post("/answer", response_class=HTMLResponse, response_model=None, name="contest_clarifications_answer_submit")
async def answer_submit(
    request: Request,
    flash: FlashDep,
    ctx: ContestContext = Depends(get_contest_context),
    clarification_id: str = Form(""),
    answer: str = Form(""),
    is_contest_public: str = Form(""),
    action: str = Form("submit"),
) -> Response:
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, _JUDGE_ONLY)
    assert isinstance(ctx.actor, User)
    slug = ctx.contest.login_slug

    clari = await get_clarification(ctx.session, ctx.contest, clarification_id)
    if clari is None:
        flash("Clarification not found.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)

    if action == "release":
        try:
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
        except ClarificationNotAcquiredByActorError:
            flash("You do not hold the lock on this clarification.", FlashCategory.DANGER)
            return RedirectResponse(url=f"/c/{slug}/clarifications/#{clarification_id}", status_code=303)

    answer_stripped = answer.strip()
    errors: list[str] = []
    if not answer_stripped:
        errors.append("Answer is required and cannot be blank.")

    if errors:
        problem_map = await _build_problem_map(ctx.session, ctx.contest)
        return _html(
            templates.TemplateResponse(
                request,
                "admin/clarifications/answer.html",
                {
                    "current_user": ctx.actor,
                    "contest": ctx.contest,
                    "clarification": clari,
                    "problem_map": problem_map,
                    "errors": errors,
                    "form_data": {"answer": answer, "is_contest_public": is_contest_public},
                    "lock_service_available": request.app.state.valkey_runtime.is_available,
                },
                status_code=422,
            )
        )

    is_contest_public_flag = is_contest_public.strip().lower() == "on"
    try:
        await answer_clarification(
            ctx.session,
            ctx.contest,
            ctx.actor,
            clari,
            request.app.state.valkey_runtime,
            answer=answer_stripped,
            is_contest_public=is_contest_public_flag,
        )
        await ctx.session.commit()
    except ClarificationAcquisitionTimeoutError:
        await ctx.session.commit()
        flash(
            "Your acquisition window expired. The lock has been released — you may re-acquire the clarification.",
            FlashCategory.WARNING,
        )
        return RedirectResponse(url=f"/c/{slug}/clarifications/#{clarification_id}", status_code=303)
    except (
        ClarificationNotAcquiredByActorError,
        ClarificationHiddenError,
        ClarificationAlreadyAnsweredError,
        ContestNotRunningError,
    ) as exc:
        problem_map = await _build_problem_map(ctx.session, ctx.contest)
        return _html(
            templates.TemplateResponse(
                request,
                "admin/clarifications/answer.html",
                {
                    "current_user": ctx.actor,
                    "contest": ctx.contest,
                    "clarification": clari,
                    "problem_map": problem_map,
                    "errors": [str(exc)],
                    "form_data": {"answer": answer, "is_contest_public": is_contest_public},
                    "lock_service_available": request.app.state.valkey_runtime.is_available,
                },
                status_code=422,
            )
        )

    flash("Clarification answered successfully.", FlashCategory.SUCCESS)
    return RedirectResponse(url=f"/c/{slug}/clarifications/#{clarification_id}", status_code=303)
