#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin routes for the per-problem custom interactive validator.

The problem edit form stages a validator candidate as part of its single Save
(see ``admin_problems.admin_problem_update``). These routes cover what that form
cannot express: downloading the current source, the HTMX-polled compile status,
and removing an active validator.

``arena_admin_problem_validator_upload`` remains for direct/API use; the edit
page no longer renders a form that posts to it while a validator is configured.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_problem_editor
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_common import get_problem_or_403
from arena.services.admin_problem_validator_service import stage_validator_source
from shared.db_schema import languages as languages_table
from shared.services.custom_validator import remove_validator, status_view
from shared.services.valkey_service import enqueue_custom_validator_validation_job

router = APIRouter(prefix="/admin", tags=["arena-admin"])


@router.post("/problems/{problem_id}/validator", name="arena_admin_problem_validator_upload")
async def arena_admin_problem_validator_upload(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    language_id: str = Form(...),
    source_file: UploadFile = File(...),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Stage and enqueue an Arena validator revision."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    edit_url = request.url_for("arena_admin_problem_edit", problem_id=problem.id)
    try:
        job = await stage_validator_source(
            session,
            problem,
            language_id=language_id,
            source_file=source_file,
        )
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(edit_url, 303)
    if job is None:
        flash("Choose a validator language and source file together.", FlashCategory.DANGER)
        return RedirectResponse(edit_url, 303)
    await session.commit()
    await enqueue_custom_validator_validation_job(request.app.state.valkey_runtime, job)
    flash("Custom validator queued for compilation.", FlashCategory.SUCCESS)
    return RedirectResponse(edit_url, 303)


@router.get("/problems/{problem_id}/validator/source", name="arena_admin_problem_validator_download")
async def arena_admin_problem_validator_download(
    problem_id: str,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Download the current validator source (active revision, else candidate)."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    validator = problem.custom_validator
    if validator is None:
        raise HTTPException(404, "This problem has no custom validator.")
    source: str | None
    language_id: str | None
    if validator.active_source is not None:
        source, language_id = validator.active_source, validator.active_language_id
    else:
        source, language_id = validator.candidate_source, validator.candidate_language_id
    if source is None or language_id is None:
        raise HTTPException(404, "This problem has no custom validator.")
    source_filename = await session.scalar(
        select(languages_table.c.source_filename).where(languages_table.c.id == language_id)
    )
    filename = f"validator-{problem.arena_number}-{source_filename or 'source.txt'}"
    return Response(
        content=source.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/problems/{problem_id}/validator/status",
    response_class=HTMLResponse,
    name="arena_admin_problem_validator_status",
)
async def arena_admin_problem_validator_status(
    request: Request,
    problem_id: str,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the HTMX-polled Arena validator status partial."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    return HTMLResponse(
        request.app.state.arena_templates.get_template("admin/_validator_status.html").render(
            request=request,
            problem=problem,
            validator_status=status_view(problem.custom_validator),
        )
    )


@router.post("/problems/{problem_id}/validator/remove", name="arena_admin_problem_validator_remove")
async def arena_admin_problem_validator_remove(
    request: Request,
    problem_id: str,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Remove the active and staged Arena validator revisions."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    if problem.custom_validator is not None:
        remove_validator(problem.custom_validator)
        await session.delete(problem.custom_validator)
        await session.commit()
    return RedirectResponse(request.url_for("arena_admin_problem_edit", problem_id=problem.id), 303)
