#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from dataclasses import replace
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from shared.enumerations import RoleEnum
from web.dependencies import ContestContext, get_contest_context
from web.models.contest import Contest
from web.models.site import Site
from web.models.users import UberAdmin, User
from web.services.assorted_utils import format_site_identity
from web.services.scoreboard import ScoreboardService, ScoreboardSnapshot
from web.services.site_service import list_contest_sites

router = APIRouter(prefix="/c/{slug}/scoreboard", tags=["contest_score"])

_service = ScoreboardService()


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


def _access_blocked(actor: UberAdmin | User, contest: Contest) -> bool:
    """ADMIN and JUDGE always see the scoreboard; STAFF, TEAM, and USER only after the contest starts."""
    if isinstance(actor, UberAdmin) or actor.role in (RoleEnum.ADMIN, RoleEnum.JUDGE):
        return False
    return not (contest.is_running or contest.is_past)


async def _build_team_display_data(
    ctx: ContestContext,
) -> tuple[dict[str, str], dict[str, str | None]]:
    """Build site-aware team labels and memberships for scoreboard rendering.

    Args:
        ctx: Contest-scoped request context.

    Returns:
        Mappings of team IDs to rendered labels and assigned site IDs.
    """
    result = await ctx.session.execute(
        select(User)
        .where(
            User.contest_id == ctx.contest.id,
            User.role == RoleEnum.TEAM,
        )
        .options(selectinload(User.site))
    )
    teams = result.scalars().all()
    display_map = {
        team.id: format_site_identity(
            team.site.sitename if team.site is not None else None,
            team.fullname,
        )
        for team in teams
    }
    site_map = {team.id: team.site_id for team in teams}
    return display_map, site_map


def _site_filter_options(actor: UberAdmin | User, sites: list[Site]) -> list[Site]:
    """Return the sites the actor may select explicitly.

    Args:
        actor: Authenticated scoreboard viewer.
        sites: Sites belonging to the current contest.

    Returns:
        The actor's assigned site, or every contest site for an unassigned actor.
    """
    if isinstance(actor, User) and actor.site_id is not None:
        return [site for site in sites if site.id == actor.site_id]
    return sites


def _resolve_selected_site_id(requested_site_id: str, site_options: list[Site]) -> str:
    """Resolve a requested site ID against the actor's selectable sites.

    Args:
        requested_site_id: Raw site ID supplied in the query string.
        site_options: Sites the actor may select.

    Returns:
        The accepted site ID, or an empty string for the all-sites view.
    """
    allowed_site_ids = {site.id for site in site_options}
    return requested_site_id if requested_site_id in allowed_site_ids else ""


def _filter_snapshot_by_site(
    snapshot: ScoreboardSnapshot,
    team_site_map: dict[str, str | None],
    selected_site_id: str,
) -> ScoreboardSnapshot:
    """Copy a scoreboard snapshot with only teams from the selected site.

    Args:
        snapshot: Contest-wide cached scoreboard snapshot.
        team_site_map: Mapping of team IDs to their assigned site IDs.
        selected_site_id: Site whose teams should remain, or empty for all sites.

    Returns:
        The original snapshot for all sites, or a filtered copy that preserves
        global ranks.
    """
    if not selected_site_id:
        return snapshot
    selected_team_ids: set[str] = set()
    for team_id, team_site_id in team_site_map.items():
        if team_site_id == selected_site_id:
            selected_team_ids.add(team_id)
    standings = []
    for standing in snapshot.standings:
        if standing.team_id in selected_team_ids:
            standings.append(standing)
    return replace(snapshot, standings=standings)


@router.get("/", response_class=HTMLResponse, name="contest_score")
async def view(
    request: Request,
    ctx: Annotated[ContestContext, Depends(get_contest_context)],
    site_id: Annotated[str, Query(max_length=64)] = "",
) -> HTMLResponse:
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
    sites = await list_contest_sites(ctx.session, contest.id)
    site_options = _site_filter_options(actor, sites)
    show_site_filter = len(sites) > 1
    actor_has_site = isinstance(actor, User) and actor.site_id is not None
    assigned_site = site_options[0] if actor_has_site and site_options else None
    selected_site_id = _resolve_selected_site_id(site_id, site_options) if show_site_filter else ""
    selected_site = next(
        (site for site in site_options if site.id == selected_site_id),
        None,
    )
    team_display_map, team_site_map = await _build_team_display_data(ctx)
    snapshot = _filter_snapshot_by_site(snapshot, team_site_map, selected_site_id)
    all_sites_url = request.url_for("contest_score", slug=contest.login_slug)
    refresh_url = all_sites_url
    if selected_site_id:
        refresh_url = refresh_url.include_query_params(site_id=selected_site_id)
    assigned_site_url = None
    if assigned_site is not None:
        assigned_site_url = all_sites_url.include_query_params(site_id=assigned_site.id)

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
                "site_options": site_options,
                "show_site_filter": show_site_filter,
                "selected_site_id": selected_site_id,
                "selected_site_name": selected_site.sitename if selected_site is not None else None,
                "assigned_site": assigned_site,
                "all_sites_url": all_sites_url,
                "assigned_site_url": assigned_site_url,
                "scoreboard_refresh_url": refresh_url,
                "viewer_role": viewer_role,
                "is_final_scoreboard": is_final_scoreboard,
            },
        )
    )
