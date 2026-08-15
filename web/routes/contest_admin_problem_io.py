#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from functools import partial

import anyio
from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from starlette.background import BackgroundTask

from shared.enumerations import ProblemValidatorType
from shared.services.custom_validator import build_validation_job
from shared.services.imageprocessing_service import ImageProcessingError
from shared.services.problem_package import PackageError, open_problem_package
from shared.services.problem_package.upload import (
    safe_package_filename,
    spool_upload,
    temporary_package_path,
)
from shared.services.sample_problem_package import SAMPLE_PACKAGE_FILENAME, build_sample_problem_package
from shared.services.valkey_service import enqueue_custom_validator_validation_job
from web.config import settings
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.routes.contest_admin_problem_helpers import (
    _html,
    _is_edit_allowed,
    _redirect,
)
from web.services.problem_service import (
    build_problem_export,
    get_active_statement_path,
    get_language_limits_map,
    get_problem_in_contest,
    import_problem_package,
)

router = APIRouter(prefix="/c/{slug}/admin/problems", tags=["contest_admin_problems"])


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@router.get("/import", response_class=HTMLResponse, name="import_problem_form")
async def import_problem_form(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/import.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "is_edit_allowed": _is_edit_allowed(ctx.contest),
            },
        )
    )


@router.get("/import/sample", name="download_sample_problem_package")
async def download_sample_problem_package(
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> Response:
    """Download the reference \"A + B\" problem package."""
    del ctx
    with temporary_package_path() as destination:
        await anyio.to_thread.run_sync(build_sample_problem_package, destination)
    return FileResponse(
        destination,
        media_type="application/zip",
        filename=SAMPLE_PACKAGE_FILENAME,
        background=BackgroundTask(destination.unlink, missing_ok=True),
    )


@router.post("/import", response_class=HTMLResponse, response_model=None, name="import_problem_submit")
async def import_problem_submit(
    request: Request,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    zip_file: UploadFile = File(...),
) -> HTMLResponse | RedirectResponse:
    if not _is_edit_allowed(ctx.contest):
        flash("Contest is not editable.", FlashCategory.DANGER)
        return _redirect(str(request.url_for("import_problem_form", slug=ctx.contest.login_slug)))
    try:
        # The upload is spooled to disk in bounded chunks and the archive is
        # opened exactly once; nothing here ever holds the package in RAM.
        async with spool_upload(zip_file) as zip_path:
            # Scanning, extracting, hashing, and validating are blocking I/O and
            # CPU: they run in a worker thread so a large package cannot stall
            # the event loop. This side then owns closing the staging area.
            staged = await anyio.to_thread.run_sync(open_problem_package, zip_path)
            try:
                import_result = await import_problem_package(
                    ctx.session,
                    ctx.contest,
                    staged.package,
                    settings.PROBLEM_TESTCASE_DIR,
                    settings.PROBLEM_STATEMENT_DIR,
                    request.app.state.image_service,
                )
            finally:
                await anyio.to_thread.run_sync(staged.staging.close)
    except (ImageProcessingError, ValueError) as exc:
        await ctx.session.rollback()
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(str(request.url_for("import_problem_form", slug=ctx.contest.login_slug)))
    if import_result.validator_candidate_token is not None:
        await enqueue_custom_validator_validation_job(
            request.app.state.valkey_runtime,
            build_validation_job(
                domain="contest",
                problem_id=import_result.problem.id,
                candidate_token=import_result.validator_candidate_token,
            ),
        )
    for warning in import_result.warnings:
        flash(warning.message, FlashCategory.WARNING)
    # An interactive problem shows sample interactions instead of sample test cases,
    # so one imported without any has nothing public to show a contestant. This
    # reads the imported problem's stored strategy, not the package's validator.
    if (
        import_result.problem.validator_type is ProblemValidatorType.INTERACTIVE
        and import_result.imported_interaction_count == 0
    ):
        flash(
            "This package has a custom validator but no sample interactions, so the problem "
            "shows no examples. Add them on the edit page below.",
            FlashCategory.WARNING,
        )
    return _redirect(
        str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=import_result.problem.id))
    )


# ---------------------------------------------------------------------------
# PDF serve / export
# ---------------------------------------------------------------------------


@router.get("/{problem_id}/statement", name="problem_statement")
async def problem_statement(
    request: Request,
    problem_id: str,
    download: str | None = Query(default=None),
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> Response:
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")

    active_path = await anyio.to_thread.run_sync(
        lambda: get_active_statement_path(problem.id, settings.PROBLEM_STATEMENT_DIR)
    )
    if active_path is None:
        return Response(content="Statement not found", status_code=404)

    content = await anyio.to_thread.run_sync(active_path.read_bytes)
    if active_path.suffix == ".md":
        filename = f"{problem.title}-statement.md"
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
    disposition = "attachment" if download else "inline"
    filename = f"{problem.title}-statement.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@router.get("/{problem_id}/export", name="export_problem")
async def export_problem(
    request: Request,
    problem_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> Response:
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")

    statement_dir = settings.PROBLEM_STATEMENT_DIR
    active_stmt = await anyio.to_thread.run_sync(lambda: get_active_statement_path(problem.id, statement_dir))
    if active_stmt is None:
        return Response(content="Statement file is missing — cannot export.", status_code=409)

    limits_map = await get_language_limits_map(ctx.session, problem)
    testcase_dir = settings.PROBLEM_TESTCASE_DIR

    with temporary_package_path() as destination:
        try:
            await anyio.to_thread.run_sync(
                partial(
                    build_problem_export,
                    problem,
                    testcase_dir,
                    statement_dir,
                    destination,
                    profile="full",
                    language_limits=limits_map,
                )
            )
        except PackageError as exc:
            destination.unlink(missing_ok=True)
            return Response(content=str(exc), status_code=409)
    return FileResponse(
        destination,
        media_type="application/zip",
        filename=safe_package_filename(problem.title),
        background=BackgroundTask(destination.unlink, missing_ok=True),
    )
