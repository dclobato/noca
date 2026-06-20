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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from shared.enumerations import RoleEnum
from web.dependencies import ContestContext, ensure_allowed_role, get_contest_context
from web.services.contest_report_service import ALL_VERDICTS, compute_contest_report
from web.services.problem_service import get_active_languages
from web.services.submission_service import list_submissions

router = APIRouter(prefix="/c/{slug}/reports", tags=["contest_reports"])

_ALLOWED = (RoleEnum.UBERADMIN, RoleEnum.ADMIN, RoleEnum.JUDGE)


def _html(response: object) -> HTMLResponse:
    return cast(HTMLResponse, response)


@router.get("/", response_class=HTMLResponse, name="contest_reports")
async def view(
    request: Request,
    ctx: ContestContext = Depends(get_contest_context),
) -> HTMLResponse:
    """Render the contest reports page with aggregated analytics."""
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

    submissions, languages = await asyncio.gather(
        list_submissions(ctx.session, contest, ctx.actor),
        get_active_languages(ctx.session),
    )
    report = compute_contest_report(contest, submissions, languages)

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
    }

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
            },
        )
    )
