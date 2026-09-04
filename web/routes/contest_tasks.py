#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from shared.enumerations import RoleEnum
from web.config import settings
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.models.users import User
from web.routes.contest_tasks_helpers import _access_blocked, _build_template_context, _ensure_task_access, _html
from web.services.task_service import (
    ContestNotRunningError,
    DuplicatePrintTaskError,
    OpenSosTaskLimitError,
    PrintRequestsDisabledError,
    TaskRateLimitError,
    create_print_task,
    create_sos_task,
)
from web.services.user_read_rate_limit import web_user_read_rate_limit

router = APIRouter(prefix="/c/{slug}/tasks", tags=["contest_tasks"], dependencies=[Depends(web_user_read_rate_limit)])


def _rate_limit_message(action: str, next_allowed_at: datetime) -> str:
    """Word a windowed refusal the way the submission limiter does.

    Args:
        action: What the team was trying to do, e.g. ``"SOS request"``.
        next_allowed_at: When the oldest in-window task leaves the window.

    Returns:
        The flash text, naming the next allowed time in server-local time.
    """
    return f"{action} limit reached. You can try again after {next_allowed_at.astimezone().strftime('%H:%M:%S')}."


@router.get("/", response_class=HTMLResponse, name="contest_tasks")
async def view(request: Request, ctx: ContestContext = Depends(get_contest_context)) -> HTMLResponse:
    """Main tasks page — role-aware view for TEAM, STAFF, chief judge, ADMIN, and UBERADMIN.

    Args:
        request: The incoming HTTP request.
        ctx: The contest context.

    Returns:
        The rendered tasks page.
    """
    templates = request.app.state.templates
    _ensure_task_access(ctx.actor, ctx.contest)
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
                    "can_handle_tasks": False,
                    "can_force_release": False,
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
    _ensure_task_access(ctx.actor, ctx.contest)
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
        await create_sos_task(
            ctx.session,
            ctx.contest,
            ctx.actor,
            rate_limit_window_seconds=settings.TEAM_TASK_RATE_LIMIT_WINDOW_SECONDS,
            rate_limit_max_tasks=settings.TEAM_TASK_RATE_LIMIT_MAX_SOS_REQUESTS,
            max_open_tasks=settings.TEAM_TASK_RATE_LIMIT_MAX_OPEN_SOS,
        )
        await ctx.session.commit()
        flash("SOS request submitted.", FlashCategory.SUCCESS)
    except ContestNotRunningError:
        flash("Tasks can only be created while the contest is running.", FlashCategory.DANGER)
    except OpenSosTaskLimitError as exc:
        flash(
            f"You already have {exc.open_count} open SOS requests. Wait until staff closes one before asking again.",
            FlashCategory.DANGER,
        )
    except TaskRateLimitError as exc:
        flash(_rate_limit_message("SOS request", exc.next_allowed_at), FlashCategory.DANGER)

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

    # No throttle preflight here. Reading the upload cannot be avoided anyway --
    # the multipart parser has already received and spooled the whole body before
    # this handler runs -- and checking the limit early would refuse a duplicate
    # with the wrong message, since detecting one requires the source hash.
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
            rate_limit_window_seconds=settings.TEAM_TASK_RATE_LIMIT_WINDOW_SECONDS,
            rate_limit_max_tasks=settings.TEAM_TASK_RATE_LIMIT_MAX_PRINT_REQUESTS,
        )
        await ctx.session.commit()
        flash("Print request submitted.", FlashCategory.SUCCESS)
    except ContestNotRunningError:
        flash("Tasks can only be created while the contest is running.", FlashCategory.DANGER)
    except TaskRateLimitError as exc:
        flash(_rate_limit_message("Print request", exc.next_allowed_at), FlashCategory.DANGER)
    except DuplicatePrintTaskError:
        flash("A pending print request for this source code already exists.", FlashCategory.WARNING)
    except PrintRequestsDisabledError:
        flash("Print requests are currently disabled for this contest.", FlashCategory.DANGER)
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)

    return RedirectResponse(url=f"/c/{slug}/tasks/", status_code=303)
