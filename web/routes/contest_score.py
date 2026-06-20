#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from typing import cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from shared.enumerations import RoleEnum
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.services.assorted_utils import format_site_identity
from web.services.scoreboard import ScoreboardService

router = APIRouter(prefix="/c/{slug}/scoreboard", tags=["contest_score"])

_service = ScoreboardService()


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


def _access_blocked(actor: UberAdmin | User, contest: Contest) -> bool:
    """ADMIN and JUDGE always see the scoreboard; STAFF, TEAM, and USER only after the contest starts."""
    if isinstance(actor, UberAdmin) or actor.role in (RoleEnum.ADMIN, RoleEnum.JUDGE):
        return False
    return not (contest.is_running or contest.is_past)


async def _build_team_display_map(ctx: ContestContext) -> dict[str, str]:
    """Build site-aware team labels for scoreboard rendering.

    Args:
        ctx: Contest-scoped request context.

    Returns:
        Mapping of team ID to the rendered team label used in the scoreboard.
    """
    result = await ctx.session.execute(
        select(User)
        .where(
            User.contest_id == ctx.contest.id,
            User.role == RoleEnum.TEAM,
        )
        .options(selectinload(User.site))
    )
    return {
        team.id: format_site_identity(
            team.site.sitename if team.site is not None else None,
            team.fullname,
        )
        for team in result.scalars().all()
    }


@router.get("/", response_class=HTMLResponse, name="contest_score")
async def view(request: Request, ctx: ContestContext = Depends(get_contest_context)) -> HTMLResponse:
    """Render the contest scoreboard.

    During the contest: admin/judge see the live scoreboard; others see the frozen view.

    After the contest ends:
    - If ``Contest.release_scoreboard_after_end`` is True: everyone sees the permanent
      final scoreboard (all results revealed, "Contest Final Scoreboard" badge).
    - Otherwise: everyone sees the frozen scoreboard ("SCOREBOARD FROZEN" badge).
    """
    templates = request.app.state.templates
    actor = ctx.actor
    contest = ctx.contest
    valkey = request.app.state.valkey_runtime

    if _access_blocked(actor, contest):
        return _html(
            templates.TemplateResponse(
                request,
                "contest/scoreboard.html",
                {
                    "current_user": actor,
                    "contest": contest,
                    "access_blocked": True,
                    "snapshot": None,
                    "team_display_map": {},
                    "viewer_role": "public",
                    "is_final_scoreboard": False,
                },
            )
        )

    is_admin = isinstance(actor, UberAdmin) or (
        hasattr(actor, "role") and actor.role in (RoleEnum.ADMIN, RoleEnum.JUDGE)
    )
    is_final_scoreboard = False

    if contest.is_past:
        if contest.release_scoreboard_after_end:
            snapshot = await _service.get_or_compute_final(contest, ctx.session, valkey)
            is_final_scoreboard = True
            viewer_role = "admin" if is_admin else "public"
        else:
            # Contest ended but not released — force frozen view for everyone
            snapshot = await _service.get_cached_or_compute(contest, "public", ctx.session, valkey)
            viewer_role = "public"
    else:
        viewer_role = "admin" if is_admin else "public"
        snapshot = await _service.get_cached_or_compute(contest, viewer_role, ctx.session, valkey)
    team_display_map = await _build_team_display_map(ctx)

    return _html(
        templates.TemplateResponse(
            request,
            "contest/scoreboard.html",
            {
                "current_user": actor,
                "contest": contest,
                "access_blocked": False,
                "snapshot": snapshot,
                "team_display_map": team_display_map,
                "viewer_role": viewer_role,
                "is_final_scoreboard": is_final_scoreboard,
            },
        )
    )
