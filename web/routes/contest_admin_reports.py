#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from shared.enumerations import RoleEnum
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.routes.contest_admin_helpers import _html
from web.services.contest_service import contest_status_label
from web.services.contest_user_service import get_contest_user_groups
from web.services.judging_service import get_chief_judge_admin_panel

router = APIRouter(prefix="/c/{slug}/admin", tags=["contest_admin"])


@router.get("/users", response_class=HTMLResponse)
async def manage_users(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    groups = await get_contest_user_groups(ctx.session, ctx.contest)
    chief_judge_panel = await get_chief_judge_admin_panel(ctx.session, ctx.contest)
    current_admin_user_id = ctx.actor.id if ctx.actor.role == RoleEnum.ADMIN else None

    return _html(
        templates.TemplateResponse(
            request,
            "admin/users/enrolled.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "admin_users": groups.admin_users,
                "judge_users": groups.judge_users,
                "staff_users": groups.staff_users,
                "team_users": groups.team_users,
                "user_users": groups.user_users,
                "owner_user_id": ctx.contest.owner_user_id,
                "current_admin_user_id": current_admin_user_id,
                "is_running": ctx.contest.is_running,
                "contest_status": contest_status_label(ctx.contest),
                "judges": chief_judge_panel.judges,
                "current_chief_judge": chief_judge_panel.current_chief_judge,
                "can_remove_chief_judge": chief_judge_panel.can_remove,
            },
        )
    )
