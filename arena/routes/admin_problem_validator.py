#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena judgment-data routes for a problem's interactive validator.

The dedicated page uploads and removes validator revisions immediately, and
also exposes source download/view and the HTMX-polled compile status.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_problem_editor
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_common import get_problem_or_403
from arena.routes.admin_problem_judgment_urls import judgment_page_url
from arena.services import admin_problem_interaction_service
from arena.services.admin_problem_validator_service import stage_validator_source
from shared.language_configs import default_extension_for_language
from shared.language_registry import highlightjs_language_for_language_id
from shared.services.custom_validator import current_validator_source, remove_validator, status_view
from shared.services.public_export_generation import bump_public_export_generation
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
    # Back to the page that owns the validator, not the test cases: an upload that
    # was refused has to be corrected here, and one that was accepted is watched
    # here while it compiles.
    edit_url = judgment_page_url(request, problem.id, "validator")
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
    await bump_public_export_generation(session, "arena", problem.id)
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
    validator_source = current_validator_source(problem.custom_validator)
    if validator_source is None:
        raise HTTPException(404, "This problem has no custom validator.")
    filename = f"validator-{problem.arena_number}{default_extension_for_language(validator_source.language_id)}"
    return Response(
        content=validator_source.source.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/problems/{problem_id}/validator/source/view",
    response_class=HTMLResponse,
    name="arena_admin_problem_validator_source_view",
)
async def arena_admin_problem_validator_source_view(
    request: Request,
    problem_id: str,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the current validator source with syntax highlighting."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    validator_source = current_validator_source(problem.custom_validator)
    if validator_source is None:
        raise HTTPException(404, "This problem has no custom validator.")
    highlight_language = highlightjs_language_for_language_id(validator_source.language_id)
    return HTMLResponse(
        request.app.state.arena_templates.get_template("admin/validator_source.html").render(
            request=request,
            current_user=current_user,
            problem=problem,
            problem_label=str(problem.arena_number),
            source_code=validator_source.source,
            highlight_language=highlight_language,
            highlight_language_class=f"language-{highlight_language}",
            highlight_theme_path="highlight/styles/github.min.css",
            highlight_core_path="highlight/highlight.min.js",
            highlight_language_path=(
                None if highlight_language == "plaintext" else f"highlight/languages/{highlight_language}.min.js"
            ),
            highlight_line_numbers_path="highlight/plugins/highlightjs-line-numbers.min.js",
        )
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
    flash: FlashDep,
    keep_interactions: Literal["true", "false"] = Form(...),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Remove the active and staged Arena validator revisions.

    Without its validator the problem's sample interactions have nothing to
    illustrate, so the caller must state what happens to them. ``keep_interactions``
    is deliberately an exact ``"true"``/``"false"`` string rather than a ``bool``:
    FastAPI would coerce ``1``, ``on`` and ``yes`` too, and the choice between
    hiding data and destroying it must not hinge on a spelling.
    """
    problem = await get_problem_or_403(problem_id, current_user, session)
    # Back to the page that owns the validator, not the test cases: an upload that
    # was refused has to be corrected here, and one that was accepted is watched
    # here while it compiles.
    edit_url = judgment_page_url(request, problem.id, "validator")
    if problem.custom_validator is None:
        return RedirectResponse(edit_url, 303)

    keep = keep_interactions == "true"
    affected = (
        await admin_problem_interaction_service.hide_interactions(session, problem.id)
        if keep
        else await admin_problem_interaction_service.delete_all_interactions(session, problem.id)
    )
    remove_validator(problem.custom_validator)
    await session.delete(problem.custom_validator)
    await bump_public_export_generation(session, "arena", problem.id)
    await session.commit()

    flash("Custom validator removed.", FlashCategory.SUCCESS)
    if affected:
        flash(
            f"{affected} sample interaction(s) were "
            + ("hidden; they will resurface if you add a validator again." if keep else "permanently deleted."),
            FlashCategory.INFO if keep else FlashCategory.WARNING,
        )
    return RedirectResponse(edit_url, 303)
