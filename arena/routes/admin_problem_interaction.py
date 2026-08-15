#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena judgment-data routes for a problem's sample interactions.

An interactive problem has no public test cases: contestants see these authored
conversations instead. Adds, edits, removals, and drag-reorders belong to the
dedicated interactions page and apply immediately.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_problem_editor
from arena.models.arena_problems import ArenaSampleInteraction
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_common import get_problem_or_403
from arena.routes.admin_problem_form_views import build_interaction_row_views
from arena.routes.admin_problem_form_views import html_response as _html
from arena.routes.admin_problem_judgment_urls import judgment_page_url
from arena.services import admin_problem_interaction_service
from shared.http_params import PG_INT32_MAX
from shared.services.editor_urls import editor_url
from shared.services.sample_interactions import InteractionParseError, parse_interaction_text, transcript_to_text

router = APIRouter(prefix="/admin", tags=["arena-admin"])


async def _get_interaction_or_404(
    session: AsyncSession,
    problem_id: str,
    si_id: str,
) -> ArenaSampleInteraction:
    """Load one of a problem's sample interactions, or raise 404."""
    interactions = await admin_problem_interaction_service.list_interactions(session, problem_id, include_hidden=True)
    interaction = next((item for item in interactions if item.id == si_id), None)
    if interaction is None:
        raise HTTPException(status_code=404, detail="Sample interaction not found")
    return interaction


@router.get(
    "/problems/{problem_id}/interactions/{si_id}/edit",
    response_class=HTMLResponse,
    name="arena_admin_problem_interaction_edit",
)
async def arena_admin_problem_interaction_edit(
    request: Request,
    problem_id: str,
    si_id: str,
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the edit form for a single sample interaction."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    interaction = await _get_interaction_or_404(session, problem.id, si_id)

    return _html(
        request.app.state.arena_templates.TemplateResponse(
            request,
            "admin/problem_interaction_form.html",
            {
                "problem": problem,
                "interaction": interaction,
                "is_edit": True,
                "is_edit_allowed": True,
                "post_action_url": str(
                    request.url_for(
                        "arena_admin_problem_interaction_update",
                        problem_id=problem_id,
                        si_id=si_id,
                    )
                ),
                "cancel_url": str(request.url_for("arena_admin_problem_edit", problem_id=problem_id)),
                "transcript_value": transcript_to_text(interaction.transcript),
                "explanation_value": interaction.explanation or "",
                "current_user": current_user,
            },
        )
    )


@router.post(
    "/problems/{problem_id}/interactions/{si_id}/edit",
    name="arena_admin_problem_interaction_update",
)
async def arena_admin_problem_interaction_update(
    request: Request,
    problem_id: str,
    si_id: str,
    flash: FlashDep,
    transcript: str = Form(""),
    explanation: str = Form(""),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Save an edited sample interaction."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    interaction = await _get_interaction_or_404(session, problem.id, si_id)
    edit_url = judgment_page_url(request, problem_id, "interactions")

    try:
        parsed = parse_interaction_text(transcript)
    except InteractionParseError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(
            request.url_for("arena_admin_problem_interaction_edit", problem_id=problem_id, si_id=si_id),
            303,
        )

    await admin_problem_interaction_service.update_interaction(
        session,
        interaction,
        transcript=parsed,
        explanation=explanation.strip() or None,
    )
    await session.commit()
    flash(f"Sample interaction #{interaction.ordinal} updated.", FlashCategory.SUCCESS)
    return RedirectResponse(editor_url(edit_url, anchor=f"si-{interaction.id}"), 303)


@router.post(
    "/problems/{problem_id}/interactions/{si_id}/move",
    response_class=HTMLResponse,
    name="arena_admin_problem_interaction_move",
)
async def arena_admin_problem_interaction_move(
    request: Request,
    problem_id: str,
    si_id: str,
    new_ordinal: int = Query(..., ge=1, le=PG_INT32_MAX),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Move a sample interaction and return the refreshed list partial."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    interaction = await _get_interaction_or_404(session, problem.id, si_id)

    await admin_problem_interaction_service.move_interaction(session, interaction, new_ordinal)
    await session.commit()

    interactions = await admin_problem_interaction_service.list_interactions(session, problem.id)
    return _html(
        request.app.state.arena_templates.TemplateResponse(
            request,
            "_partials/sample_interaction_list_table.html",
            {
                "interaction_rows": build_interaction_row_views(request, problem.id, interactions),
                "is_edit_allowed": True,
            },
        )
    )
