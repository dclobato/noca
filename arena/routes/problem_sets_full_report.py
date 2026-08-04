#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena class-wide problem-set report routes (HTML page and CSV download)."""

from __future__ import annotations

import csv
import io
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.routes.class_route_guards import html, problem_set_list_url, require_problem_set_manager
from arena.services import arena_class_full_report_service
from arena.services.arena_class_full_report_service import ClassFullReport
from arena.services.arena_problem_set_service import (
    ArenaProblemSetNotFoundError,
    ArenaProblemSetPermissionError,
)

router = APIRouter(tags=["arena-classes"])

#: Excel only reads a UTF-8 CSV as UTF-8 when it starts with a byte-order mark.
_UTF8_BOM = "\ufeff"

#: Leading characters that make a spreadsheet treat a cell as a formula rather
#: than as text. A student named ``=cmd|...`` must never become executable
#: content in someone's spreadsheet.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _safe_text(value: str) -> str:
    """Neutralize a text cell that a spreadsheet would otherwise run as a formula.

    CSV quoting does not help here: a spreadsheet strips the quotes and then
    evaluates what is inside. The portable fix is to push the value off the
    formula grammar with a leading apostrophe, which Excel, LibreOffice, and
    Sheets all consume as "this is literal text".
    """
    return f"'{value}" if value.startswith(_FORMULA_TRIGGERS) else value


async def _build_report(session: AsyncSession, user: ArenaUser, class_id: str) -> ClassFullReport:
    """Build the class report, mapping service errors onto HTTP status codes."""
    try:
        return await arena_class_full_report_service.build_class_full_report(
            session,
            actor_id=user.id,
            actor_role=user.role,
            class_id=class_id,
            now=datetime.now(UTC),
        )
    except ArenaProblemSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Class not found") from exc
    except ArenaProblemSetPermissionError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc


def _csv_filename(class_name: str) -> str:
    """Build a safe ASCII download filename from the class name."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", class_name).strip("-").lower()
    return f"{slug or 'class'}-full-report.csv"


def _format_rate(rate: float | None) -> str:
    """Render an AC rate for a spreadsheet cell: a bare number, or empty."""
    return "" if rate is None else f"{rate:.1f}"


@router.get(
    "/classes/{class_id}/problem-sets/report",
    response_class=HTMLResponse,
    name="arena_class_full_report",
)
async def class_full_report(
    request: Request,
    class_id: str,
    page: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the class-wide students x problem-sets AC-rate report.

    Accessible to the class teacher and ARENA_ADMINs. Only problem sets whose
    deadline has passed take part, so the matrix describes finished work.

    Args:
        request: Current HTTP request.
        class_id: UUID of the ``arena_classes`` row.
        page: Optional pagination context forwarded from the list page.
        sort: Optional sort context forwarded from the list page.
        direction: Optional sort direction forwarded from the list page.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        HTMLResponse: The report page, or a redirect on auth failure.

    Raises:
        HTTPException: 403 when the caller is not the teacher or an admin,
            404 when the class does not exist.
    """
    user_or_redirect, class_detail = await require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    report = await _build_report(session, user_or_redirect, class_id)
    templates = request.app.state.arena_templates
    return html(
        templates.TemplateResponse(
            request,
            "classes/class_full_report.html",
            {
                "current_user": user_or_redirect,
                "class_detail": class_detail,
                "class_id": class_id,
                "report": report,
                "back_url": problem_set_list_url(
                    request,
                    class_id=class_id,
                    page=page,
                    sort=sort,
                    direction=direction,
                ),
                "csv_url": str(request.url_for("arena_class_full_report_csv", class_id=class_id)),
            },
        )
    )


@router.get(
    "/classes/{class_id}/problem-sets/report/csv",
    name="arena_class_full_report_csv",
)
async def class_full_report_csv(
    request: Request,
    class_id: str,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Download the class-wide report as CSV.

    Rates are written as bare one-decimal numbers (no percent sign) so a
    spreadsheet can compute on them; an empty cell means the problem set has no
    problems, which is also how the page renders it.

    Args:
        request: Current HTTP request.
        class_id: UUID of the ``arena_classes`` row.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        Response: A ``text/csv`` attachment, or a redirect on auth failure.

    Raises:
        HTTPException: 403 when the caller is not the teacher or an admin,
            404 when the class does not exist.
    """
    user_or_redirect, _ = await require_problem_set_manager(
        request,
        current_user,
        class_id=class_id,
        session=session,
    )
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect
    report = await _build_report(session, user_or_redirect, class_id)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Student", *(_safe_text(f"[{column.index}] {column.name}") for column in report.sets), "Total"],
    )
    for row in report.students:
        writer.writerow(
            [
                _safe_text(row.user_name),
                *(_format_rate(cell.ac_rate) for cell in row.cells),
                _format_rate(row.total_rate),
            ]
        )
    writer.writerow(
        [
            "Class average",
            *(_format_rate(rate) for rate in report.set_average_rates),
            _format_rate(report.class_average_rate),
        ]
    )
    return Response(
        content=_UTF8_BOM + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_csv_filename(report.class_name)}"'},
    )
