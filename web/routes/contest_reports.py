#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest reports route — analytics dashboard for admin/judge users."""

from __future__ import annotations

import asyncio
import json
from typing import cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from shared.enumerations import RoleEnum
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.services.contest_report_query_service import list_contest_report_problems, list_contest_report_submissions
from web.services.contest_report_service import ALL_VERDICTS, compute_contest_report
from web.services.contest_user_service import count_contest_teams, count_teams_by_site
from web.services.problem_service import get_contest_languages
from web.services.site_service import get_site_in_contest, list_contest_sites

router = APIRouter(prefix="/c/{slug}/reports", tags=["contest_reports"])

_ALLOWED = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE)


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


@router.get("/", response_class=HTMLResponse, name="contest_reports")
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

    sites, team_counts_by_site, total_enrolled_teams, selected_site = await asyncio.gather(
        list_contest_sites(ctx.session, contest.id),
        count_teams_by_site(ctx.session, contest),
        count_contest_teams(ctx.session, contest),
        get_site_in_contest(ctx.session, contest, site),
    )
    site_id = selected_site.id if selected_site else None
    enrolled_teams = team_counts_by_site.get(site_id, 0) if site_id else total_enrolled_teams

    submissions, problems, languages = await asyncio.gather(
        list_contest_report_submissions(ctx.session, contest, site_id=site_id),
        list_contest_report_problems(ctx.session, contest),
        get_contest_languages(ctx.session, contest),
    )
    report = compute_contest_report(contest, submissions, problems, languages, enrolled_teams)

    accept_label = "AC + PE" if report.accept_pe else "AC"
    chart_data = {
        "runs_pie": [
            {"value": r.count, "name": r.problem.label, "color": "#" + r.problem.color}
            for r in report.runs_distribution
        ],
        "accepted_pie": [
            {"value": r.count, "name": r.problem.label, "color": "#" + r.problem.color}
            for r in report.accepted_distribution
        ],
        "time_labels": [w.label for w in report.time_windows],
        "time_all": [w.all_count for w in report.time_windows],
        "time_accepted": [w.accepted_count for w in report.time_windows],
        "accept_label": accept_label,
        "problem_race": [
            {
                "name": series.problem.label,
                "color": "#" + series.problem.color,
                "solved_minutes": series.solved_minutes,
            }
            for series in report.problem_race
        ],
        "duration_minutes": contest.duration_minutes,
        # Where "now" falls on the contest clock, for the two time-axis charts.
        # Both are drawn over the contest's whole duration so the axis does not
        # rescale on every reload, which leaves the part that has not happened
        # yet looking exactly like a stretch in which nobody submitted. The
        # charts mark this minute and shade past it. Only a running contest has
        # a meaningful "now": before the start there is nothing to report on,
        # and once the contest ends the whole axis is real elapsed time.
        "elapsed_minutes": (contest.elapsed_time_seconds / 60) if contest.is_running else None,
        "solved_boxplot": (
            [
                report.performance.solved_summary.minimum,
                report.performance.solved_summary.q1,
                report.performance.solved_summary.median,
                report.performance.solved_summary.q3,
                report.performance.solved_summary.maximum,
            ]
            if report.performance.solved_summary
            else None
        ),
        "solved_histogram_labels": [str(bucket.solved) for bucket in report.performance.solved_histogram],
        "solved_histogram_counts": [bucket.team_count for bucket in report.performance.solved_histogram],
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
