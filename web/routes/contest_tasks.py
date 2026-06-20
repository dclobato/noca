#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from shared.enumerations import RoleEnum
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.models.users import User
from web.routes.contest_tasks_helpers import _ALLOWED, _access_blocked, _build_template_context, _html
from web.services.task_service import (
    ContestNotRunningError,
    DuplicatePrintTaskError,
    PrintRequestsDisabledError,
    create_print_task,
    create_sos_task,
)

router = APIRouter(prefix="/c/{slug}/tasks", tags=["contest_tasks"])


@router.get("/", response_class=HTMLResponse, name="contest_tasks")
async def view(request: Request, ctx: ContestContext = Depends(get_contest_context)) -> HTMLResponse:
    """Main tasks page — role-aware view for TEAM, STAFF, ADMIN, and UBERADMIN.

    Args:
        request: The incoming HTTP request.
        ctx: The contest context.

    Returns:
        The rendered tasks page.
    """
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, _ALLOWED)
    if _access_blocked(ctx.actor, ctx.contest):
        return _html(
            templates.TemplateResponse(
                request,
                "contest/tasks.html",
                {
                    "current_user": ctx.actor,
                    "contest": ctx.contest,
                    "access_blocked": True,
                    "tasks": [],
                    "lock_service_available": request.app.state.valkey_runtime.is_available,
                    "problems": [],
                    "problem_map": {},
                },
            )
        )
    ctx_data = await _build_template_context(ctx, request)
    return _html(
        templates.TemplateResponse(
            request,
            "contest/tasks.html",
            {"current_user": ctx.actor, "contest": ctx.contest, "access_blocked": False, **ctx_data},
        )
    )


@router.get("/list", response_class=HTMLResponse, name="contest_tasks_list")
async def list_partial(request: Request, ctx: ContestContext = Depends(get_contest_context)) -> HTMLResponse:
    """HTMX partial — returns the tasks list wrapper div only.

    Used by the 60-second auto-refresh polling from TEAM and STAFF browsers.

    Args:
        request: The incoming HTTP request.
        ctx: The contest context.

    Returns:
        The rendered tasks_list.html partial.
    """
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, _ALLOWED)
    ctx_data = await _build_template_context(ctx, request)
    return _html(
        templates.TemplateResponse(
            request,
            "contest/tasks_list.html",
            {"current_user": ctx.actor, "contest": ctx.contest, **ctx_data},
        )
    )


@router.post("/sos", response_class=HTMLResponse, response_model=None, name="contest_tasks_sos")
async def create_sos(
    request: Request,
    flash: FlashDep,
    ctx: ContestContext = Depends(get_contest_context),
) -> Response:
    """Create an SOS (help request) task on behalf of the authenticated team.

    Args:
        request: The incoming HTTP request.
        flash: Flash message dependency.
        ctx: The contest context.

    Returns:
        Redirect to the tasks page (303).
    """
    ensure_allowed_role(ctx.actor, (RoleEnum.TEAM,))
    assert isinstance(ctx.actor, User)
    slug = ctx.contest.login_slug

    try:
        await create_sos_task(ctx.session, ctx.contest, ctx.actor)
        await ctx.session.commit()
        flash("SOS request submitted.", FlashCategory.SUCCESS)
    except ContestNotRunningError:
        flash("Tasks can only be created while the contest is running.", FlashCategory.DANGER)

    return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)


@router.post("/print", response_class=HTMLResponse, response_model=None, name="contest_tasks_print")
async def create_print(
    request: Request,
    flash: FlashDep,
    ctx: ContestContext = Depends(get_contest_context),
    problem_id: str = Form(""),
    source_file: UploadFile = File(...),
) -> Response:
    """Create a PRINT task so a team can receive a printed copy of their source code.

    Args:
        request: The incoming HTTP request.
        flash: Flash message dependency.
        ctx: The contest context.
        problem_id: The ID of the problem whose source should be printed.
        source_file: The uploaded source file.

    Returns:
        Redirect to the tasks page (303).
    """
    ensure_allowed_role(ctx.actor, (RoleEnum.TEAM,))
    assert isinstance(ctx.actor, User)
    slug = ctx.contest.login_slug

    if not problem_id.strip():
        flash("A problem must be selected.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)

    raw = await source_file.read()
    if not raw:
        flash("Source file is empty.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)

    source_code = raw.decode("utf-8", errors="replace")

    try:
        await create_print_task(
            ctx.session,
            ctx.contest,
            ctx.actor,
            problem_id=problem_id.strip(),
            source_code=source_code,
        )
        await ctx.session.commit()
        flash("Print request submitted.", FlashCategory.SUCCESS)
    except ContestNotRunningError:
        flash("Tasks can only be created while the contest is running.", FlashCategory.DANGER)
    except DuplicatePrintTaskError:
        flash("A pending print request for this source code already exists.", FlashCategory.WARNING)
    except PrintRequestsDisabledError:
        flash("Print requests are currently disabled for this contest.", FlashCategory.DANGER)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)

    return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)
