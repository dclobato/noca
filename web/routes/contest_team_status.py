#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Team status map: one card per team, grouped by site, refreshed every ten seconds.

Open to everyone running the venue -- uberadmin, admin, judge and staff -- which
is why it is a contest section of its own rather than an administration page:
judges and staff never reach ``/c/{slug}/admin``. The page polls its own URL
through htmx and swaps the grid, the same shape the scoreboard and the contest
dashboard use, so there is no JSON twin to keep in step with the template.

Two query parameters carry the reader's view and therefore survive the poll,
which re-fetches the *current* URL rather than the bare route:

* ``site`` scopes the board to one site (the reports page's own control); an
  unknown value is treated as "all sites", the forgiving handling a stale
  bookmark gets elsewhere.
* ``show`` (repeatable) names the sites whose online teams are unfolded. The
  board folds present teams into a count so the empty seats are what a reader
  sees first; a site lead who wants to see their room in full opens it once
  and the refresh keeps it open.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from starlette.routing import NoMatchFound

from shared.enumerations import RoleEnum
from web.config import settings
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.models.contest import Contest
from web.models.users import UberAdmin, User
from web.services.team_status_service import TeamStatusBoard, TeamStatusSiteGroup, load_team_status_board
from web.services.user_read_rate_limit import web_user_read_rate_limit

router = APIRouter(prefix="/c/{slug}", tags=["contest_team_status"], dependencies=[Depends(web_user_read_rate_limit)])

_ALLOWED = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE, RoleEnum.STAFF)
_CAN_OPEN_RECORD = (RoleEnum.UBERADMIN, RoleEnum.ADMIN)

#: How often the page re-fetches itself. Presence itself moves at the pace of
#: the 60 s contest clock, so polling faster than this would only show the same
#: board more often.
TEAM_STATUS_REFRESH_SECONDS = 10


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


def _visible_groups(
    board: TeamStatusBoard, site: str | None
) -> tuple[list[TeamStatusSiteGroup], TeamStatusSiteGroup | None]:
    """Return the groups to render and the one the reader scoped to, if any."""
    if site:
        for group in board.groups:
            if group.key == site:
                return [group], group
    return board.groups, None


def _board_url(request: Request, *, site: str | None, show: list[str]) -> str:
    """Build this page's URL for a given scope and set of unfolded sites."""
    params: list[tuple[str, str]] = []
    if site:
        params.append(("site", site))
    params.extend(("show", key) for key in show)
    query = urlencode(params)
    return f"{request.url.path}?{query}" if query else request.url.path


def _toggle_urls(
    request: Request, groups: list[TeamStatusSiteGroup], *, site: str | None, show: list[str]
) -> dict[str, str]:
    """Return, per site, the URL that folds or unfolds its online teams."""
    urls: dict[str, str] = {}
    for group in groups:
        toggled = [key for key in show if key != group.key] if group.key in show else [*show, group.key]
        urls[group.key] = _board_url(request, site=site, show=toggled)
    return urls


def _record_url_builder(request: Request, contest: Contest, actor: UberAdmin | User) -> Callable[[str], str]:
    """Return a function giving a team's admin record URL, or ``""`` when there is none to give.

    Judges and staff cannot open the enrolled-user page, so they get no link
    rather than a link to a refusal. The lookup tolerates an unmounted route the
    way ``nav_url`` does, because single-router test applications render this
    page too.
    """
    if actor.role not in _CAN_OPEN_RECORD:
        return lambda user_id: ""

    def build(user_id: str) -> str:
        try:
            return str(request.url_for("edit_user_form", slug=contest.login_slug, user_id=user_id))
        except NoMatchFound:
            return ""

    return build


@router.get("/team-status", response_class=HTMLResponse, name="contest_team_status")
async def team_status(
    request: Request,
    ctx: ContestContext = Depends(get_contest_context),
    site: str | None = Query(default=None),
    show: list[str] = Query(default=[]),
) -> HTMLResponse:
    """Render the team status board for the venue staff.

    Presence is read only when the deployment tracks it: with
    ``NOCA_WEB_PRESENCE_ENABLED`` off, or no Valkey runtime, every team that
    signed in is reported offline and the page says why.
    """
    ensure_allowed_role(ctx.actor, _ALLOWED)
    templates = request.app.state.templates

    presence = getattr(request.app.state, "valkey_runtime", None) if settings.PRESENCE_ENABLED else None
    board = await load_team_status_board(ctx.session, ctx.contest, presence=presence)

    groups, selected_site = _visible_groups(board, site)
    scope = selected_site.key if selected_site else None
    shown = [key for key in show if key]

    return _html(
        templates.TemplateResponse(
            request,
            "contest/team_status.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "board": board,
                "groups": groups,
                "selected_site": selected_site,
                "expanded": set(shown),
                "toggle_urls": _toggle_urls(request, groups, site=scope, show=shown),
                "site_urls": {group.key: _board_url(request, site=group.key, show=[]) for group in board.groups},
                "all_sites_url": _board_url(request, site=None, show=[]),
                "record_url": _record_url_builder(request, ctx.contest, ctx.actor),
                "refresh_seconds": TEAM_STATUS_REFRESH_SECONDS,
                "presence_ttl_seconds": settings.PRESENCE_TTL_SECONDS,
            },
        )
    )
