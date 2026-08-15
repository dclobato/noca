#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
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
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from arena.config import settings
from arena.database import get_db
from arena.dependencies.admin import require_arena_problem_editor
from arena.models.arena_users import ArenaUser
from arena.services import admin_problem_io_service, admin_problem_service
from shared.enumerations import ArenaRole
from shared.services.custom_validator import build_validation_job
from shared.services.imageprocessing_service import ImageProcessingError, ImageProcessingService
from shared.services.problem_package import PackageError, open_problem_package
from shared.services.problem_package.upload import (
    safe_package_filename,
    spool_upload,
    temporary_package_path,
)
from shared.services.sample_problem_package import SAMPLE_PACKAGE_FILENAME, build_sample_problem_package
from shared.services.valkey_service import enqueue_custom_validator_validation_job

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


@router.get("/problems/import/sample", name="arena_admin_problem_sample_package")
async def admin_problem_sample_package(
    current_user: ArenaUser = Depends(require_arena_problem_editor),
) -> Response:
    """Download the reference \"A + B\" problem package."""
    del current_user
    with temporary_package_path() as destination:
        await anyio.to_thread.run_sync(build_sample_problem_package, destination)
    return FileResponse(
        destination,
        media_type="application/zip",
        filename=SAMPLE_PACKAGE_FILENAME,
        background=BackgroundTask(destination.unlink, missing_ok=True),
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

    image_service: ImageProcessingService = request.app.state.image_service
    try:
        # The upload is spooled to disk in bounded chunks and the archive is
        # opened exactly once; nothing here ever holds the package in RAM.
        async with spool_upload(package) as zip_path:
            # Scanning, extracting, hashing, and validating are blocking I/O and
            # CPU: they run in a worker thread so a large package cannot stall
            # the event loop. This side then owns closing the staging area.
            staged = await anyio.to_thread.run_sync(open_problem_package, zip_path)
            try:
                result = await admin_problem_io_service.import_problem_package(
                    session,
                    staged.package,
                    caller_id=current_user.id,
                    image_service=image_service,
                    testcase_dir=settings.PROBLEM_TESTCASE_DIR,
                )
            finally:
                await anyio.to_thread.run_sync(staged.staging.close)
    except (ValueError, ImageProcessingError) as exc:
        await session.rollback()
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(url=form_url, status_code=303)

    for warning in result.warnings:
        flash(warning.message, FlashCategory.WARNING)

    problem = result.problem
    await session.refresh(problem, attribute_names=["custom_validator"])
    if problem.custom_validator is not None and problem.custom_validator.candidate_token is not None:
        await enqueue_custom_validator_validation_job(
            request.app.state.valkey_runtime,
            build_validation_job(
                domain="arena",
                problem_id=problem.id,
                candidate_token=problem.custom_validator.candidate_token,
            ),
        )

    flash(
        f"Problem #{problem.arena_number} imported (disabled). Review and complete the details below.",
        FlashCategory.SUCCESS,
    )
    # A package that stated no language had one filled in (or not) automatically,
    # so the importer is asked to verify it rather than trust it silently.
    if result.language_source == "detected" and result.statement_language is not None:
        flash(
            "The package did not state a statement language; it was detected as "
            f"{result.statement_language.label}. Please confirm it below.",
            FlashCategory.WARNING,
        )
    elif result.language_source == "undetermined":
        flash(
            "The package did not state a statement language and it could not be detected. Please select it below.",
            FlashCategory.WARNING,
        )
    # An interactive problem shows sample interactions instead of sample test cases,
    # so one imported without any has nothing public to show a contestant. This
    # reads the imported problem's stored strategy, not the package's validator.
    if result.is_interactive and result.imported_interaction_count == 0:
        flash(
            "This package has a custom validator but no sample interactions, so the problem "
            "shows no examples. Add them on the edit page below.",
            FlashCategory.WARNING,
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
    with temporary_package_path() as destination:
        try:
            await anyio.to_thread.run_sync(
                admin_problem_io_service.export_problem_package,
                problem,
                owner_name,
                settings.PROBLEM_TESTCASE_DIR,
                destination,
            )
        except PackageError as exc:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    filename = safe_package_filename(f"problem-{problem.arena_number}-{problem.title}")
    return FileResponse(
        destination,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(destination.unlink, missing_ok=True),
    )
