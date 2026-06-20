#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin routes for problem ZIP import and export.

Import accepts a problem package, sets the owner to the importing user,
preserves the package's author, marks every test case secret, and redirects to
the standard edit page. Export streams a ZIP holding all problem data.
"""

from __future__ import annotations

from typing import Any, cast

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.database import get_db
from arena.dependencies.admin import require_arena_problem_editor
from arena.models.arena_users import ArenaUser
from arena.services import admin_problem_io_service, admin_problem_service
from arena.services.admin_category_service import normalize_slug
from shared.enumerations import ArenaRole
from shared.services.imageprocessing_service import ImageProcessingError, ImageProcessingService

router = APIRouter(prefix="/admin", tags=["arena-admin"])


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


@router.get(
    "/problems/import",
    response_class=HTMLResponse,
    name="arena_admin_problem_import_form",
)
async def admin_problem_import_form(
    request: Request,
    flash: FlashDep,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
) -> Response:
    """Render the problem import upload page."""
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problem_import.html",
            {
                "current_user": current_user,
                "is_admin": current_user.role == ArenaRole.ARENA_ADMIN,
            },
        )
    )


@router.post("/problems/import", name="arena_admin_problem_import_submit")
async def admin_problem_import_submit(
    request: Request,
    flash: FlashDep,
    package: UploadFile = File(...),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Import a problem package and redirect to the edit page on success."""
    form_url = str(request.url_for("arena_admin_problem_import_form"))
    if not package or not package.filename:
        flash("Please choose a ZIP file to import.", FlashCategory.DANGER)
        return RedirectResponse(url=form_url, status_code=303)

    zip_bytes = await package.read()
    image_service: ImageProcessingService = request.app.state.image_service
    try:
        problem = await admin_problem_io_service.import_problem_from_zip(
            session,
            zip_bytes=zip_bytes,
            caller_id=current_user.id,
            image_service=image_service,
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
        )
    except (ValueError, ImageProcessingError) as exc:
        await session.rollback()
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=form_url, status_code=303)

    flash(
        f"Problem #{problem.arena_number} imported (disabled). Review and complete the details below.",
        FlashCategory.SUCCESS,
    )
    return RedirectResponse(
        url=str(request.url_for("arena_admin_problem_edit", problem_id=problem.id)),
        status_code=303,
    )


@router.get("/problems/{problem_id}/export", name="arena_admin_problem_export")
async def admin_problem_export(
    request: Request,
    problem_id: str,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Export a problem as a downloadable ZIP package."""
    is_admin = current_user.role == ArenaRole.ARENA_ADMIN
    problem = await admin_problem_service.get_problem(
        session,
        problem_id,
        caller_id=current_user.id,
        is_admin=is_admin,
    )
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found")

    owner = await session.get(ArenaUser, problem.owner_id)
    owner_name = owner.nome if owner else ""
    zip_bytes = await anyio.to_thread.run_sync(
        admin_problem_io_service.build_export_zip,
        problem,
        owner_name,
        settings.PROBLEM_TESTCASE_DIR,
    )
    filename = f"problem-{problem.arena_number}-{normalize_slug(problem.title)}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
