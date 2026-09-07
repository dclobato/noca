#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from shared.services.problem_package.upload import OwnedTemporaryFileResponse, temporary_package_path
from web.config import settings
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.routes.contest_admin_helpers import _html
from web.services.export_rate_limit import web_admin_export_rate_limit

router = APIRouter(prefix="/c/{slug}/admin", tags=["contest_admin"])


@router.get("/import_export", response_class=HTMLResponse)
async def import_export(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/import_export.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
            },
        )
    )


@router.get("/export-animeitor", name="export_animeitor", dependencies=[Depends(web_admin_export_rate_limit)])
async def export_animeitor(
    request: Request,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> Response:
    """Download a ZIP file compatible with the maratona-animeitor consumer."""
    from web.services.animeitor_export_service import AnimeitorExportError, write_animeitor_zip

    try:
        with temporary_package_path() as destination:
            filename = await write_animeitor_zip(ctx.session, ctx.contest, destination)
    except AnimeitorExportError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(
            url=str(request.url_for("import_export", slug=ctx.contest.login_slug)),
            status_code=303,
        )

    return OwnedTemporaryFileResponse(
        destination,
        media_type="application/zip",
        filename=filename,
    )


@router.get("/export-events", name="export_contest_timeline", dependencies=[Depends(web_admin_export_rate_limit)])
async def export_contest_timeline(
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> Response:
    """Download a markdown contest timeline built from persisted contest history."""
    from web.services.contest_timeline_export_service import build_contest_timeline_report

    filename, content = await build_contest_timeline_report(ctx.session, ctx.contest)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/users-per-site-report", name="users_per_site_report", dependencies=[Depends(web_admin_export_rate_limit)])
async def users_per_site_report(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> Response:
    """Download a markdown report of contest users grouped by site."""
    from web.services.users_per_site_report_service import build_users_per_site_report

    login_url_obj = request.url_for("contest_login_get", slug=ctx.contest.login_slug)
    login_url = (settings.WEB_URL_BASE + str(login_url_obj.path)) if settings.WEB_URL_BASE else str(login_url_obj)
    filename, content = await build_users_per_site_report(ctx.session, ctx.contest, login_url)
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
