#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest reports route — analytics dashboard for admin/judge users."""

from __future__ import annotations

import json
from typing import cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from shared.enumerations import RoleEnum
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.services.contest_report_cache import get_contest_report_page_data
from web.services.contest_report_service import ALL_VERDICTS
from web.services.contest_user_service import count_contest_teams, count_teams_by_site
from web.services.export_rate_limit import web_contest_report_rate_limit
from web.services.site_service import get_site_in_contest, list_contest_sites

router = APIRouter(prefix="/c/{slug}/reports", tags=["contest_reports"])

_ALLOWED = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE)


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


@router.get(
    "/",
    response_class=HTMLResponse,
    name="contest_reports",
    dependencies=[Depends(web_contest_report_rate_limit)],
)
async def view(
    request: Request,
    ctx: ContestContext = Depends(get_contest_context),
    site: str | None = Query(default=None),
) -> HTMLResponse:
    """Render the contest reports page with aggregated analytics.

    `site` optionally scopes every figure on the page to one site's teams
    (an unrecognized or cross-contest id is silently treated as unscoped,
    the same forgiving handling a stale bookmark or hand-edited URL gets
    elsewhere on admin-only pages). Omitting it reports the whole contest.
    """
    templates = request.app.state.templates
    ensure_allowed_role(ctx.actor, _ALLOWED)
    contest = ctx.contest

    if contest.upcoming:
        return _html(
            templates.TemplateResponse(
                request,
                "admin/reports.html",
                {
                    "current_user": ctx.actor,
                    "contest": contest,
                    "access_blocked": True,
                    "report": None,
                    "chart_data_json": "{}",
                    "all_verdicts": ALL_VERDICTS,
                },
            )
        )

    sites = await list_contest_sites(ctx.session, contest.id)
    team_counts_by_site = await count_teams_by_site(ctx.session, contest)
    total_enrolled_teams = await count_contest_teams(ctx.session, contest)
    selected_site = await get_site_in_contest(ctx.session, contest, site)
    site_id = selected_site.id if selected_site else None
    enrolled_teams = team_counts_by_site.get(site_id, 0) if site_id else total_enrolled_teams

    page_data = await get_contest_report_page_data(
        ctx.session,
        contest,
        site_id=site_id,
        enrolled_teams=enrolled_teams,
        valkey=getattr(request.app.state, "valkey_runtime", None),
    )
    report = page_data.report
    chart_data = {
        **page_data.chart_data,
        "duration_minutes": contest.duration_minutes,
        "elapsed_minutes": (contest.elapsed_time_seconds / 60) if contest.is_running else None,
    }

    site_tiles = [
        {
            "id": contest_site.id,
            "name": contest_site.sitename,
            "team_count": team_counts_by_site.get(contest_site.id, 0),
        }
        for contest_site in sites
    ]

    return _html(
        templates.TemplateResponse(
            request,
            "admin/reports.html",
            {
                "current_user": ctx.actor,
                "contest": contest,
                "access_blocked": False,
                "report": report,
                "chart_data_json": json.dumps(chart_data),
                "all_verdicts": ALL_VERDICTS,
                "sites": site_tiles,
                "total_enrolled_teams": total_enrolled_teams,
                "selected_site_id": site_id,
                "selected_site_name": selected_site.sitename if selected_site else None,
            },
        )
    )
