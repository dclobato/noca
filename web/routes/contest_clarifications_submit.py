#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from shared.enumerations import RoleEnum
from web.config import settings
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.models.users import User
from web.services.clarification_service import (
    ClarificationRateLimitError,
    ContestNotRunningError,
    TooManyUnansweredClarificationsError,
    create_announcement,
    create_clarification,
)

router = APIRouter(prefix="/c/{slug}/clarifications", tags=["contest_clarifications"])


@router.post("/new", response_class=HTMLResponse, response_model=None, name="contest_clarifications_new")
async def submit_new(
    request: Request,
    flash: FlashDep,
    ctx: ContestContext = Depends(get_contest_context),
    problem_id: str = Form(""),
    question: str = Form(""),
) -> Response:
    ensure_allowed_role(ctx.actor, (RoleEnum.TEAM,))
    assert isinstance(ctx.actor, User)
    slug = ctx.contest.login_slug

    question_stripped = question.strip()

    if not question_stripped:
        flash("Question is required and cannot be blank.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)

    try:
        new_clari = await create_clarification(
            ctx.session,
            ctx.contest,
            ctx.actor,
            problem_id=problem_id.strip() or None,
            question=question_stripped,
            rate_limit_window_seconds=settings.CLARIFICATION_RATE_LIMIT_WINDOW_SECONDS,
            rate_limit_max_requests=settings.CLARIFICATION_RATE_LIMIT_MAX_REQUESTS,
            max_open_clarifications=settings.CLARIFICATION_RATE_LIMIT_MAX_UNANSWERED,
        )
        await ctx.session.commit()
    except ContestNotRunningError:
        flash("Clarifications can only be submitted while the contest is running.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)
    except TooManyUnansweredClarificationsError as exc:
        flash(
            f"You have {exc.open_count} clarifications still unanswered. "
            "Wait for a judge to answer before asking again.",
            FlashCategory.DANGER,
        )
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)
    except ClarificationRateLimitError as exc:
        next_time = exc.next_allowed_at.astimezone().strftime("%H:%M:%S")
        flash(
            f"Clarification limit reached. You can ask again after {next_time}.",
            FlashCategory.DANGER,
        )
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)
    except ValueError:
        flash("The selected problem does not belong to this contest.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)

    flash("Clarification submitted successfully.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=f"/c/{ctx.contest.login_slug}/clarifications/#{new_clari.id}",
        status_code=303,
    )


@router.post(
    "/announcement", response_class=HTMLResponse, response_model=None, name="contest_clarifications_announcement"
)
async def submit_announcement(
    request: Request,
    flash: FlashDep,
    ctx: ContestContext = Depends(get_contest_context),
    problem_id: str = Form(""),
    announcement: str = Form(""),
) -> Response:
    ensure_allowed_role(ctx.actor, (RoleEnum.ADMIN, RoleEnum.JUDGE))
    assert isinstance(ctx.actor, User)
    slug = ctx.contest.login_slug

    if not announcement.strip():
        flash("Announcement text is required and cannot be blank.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)

    try:
        new_clari = await create_announcement(
            ctx.session,
            ctx.contest,
            ctx.actor,
            problem_id=problem_id.strip() or None,
            announcement=announcement,
        )
        await ctx.session.commit()
    except ContestNotRunningError:
        flash(
            "Announcements can only be created while the contest is running, "
            "unless published by a contest admin or the chief judge.",
            FlashCategory.DANGER,
        )
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)
    except ValueError:
        flash("The selected problem does not belong to this contest.", FlashCategory.DANGER)
        return RedirectResponse(url=f"/c/{slug}/clarifications/", status_code=303)

    flash("Announcement published successfully.", FlashCategory.SUCCESS)
    return RedirectResponse(url=f"/c/{slug}/clarifications/#{new_clari.id}", status_code=303)
