#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest admin routes for a problem's sample interactions.

An interactive problem has no public test cases: what contestants see instead are
these authored conversations. They are authored on the judgment-data interactions
page, where every action on an existing transcript posts immediately; only rows
typed inline wait for that page's own Save in
``contest_admin_problem_judgment_pages.py``. Per-row edit and drag-reorder get
their own endpoints here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_flash import FlashCategory, FlashDep

from shared.enumerations import ProblemValidatorType
from shared.http_params import PG_INT32_MAX
from shared.services.custom_validator import status_view
from shared.services.editor_urls import editor_url
from shared.services.sample_interactions import (
    MAX_SAMPLE_INTERACTIONS,
    InteractionParseError,
    SampleInteractionRowView,
    parse_interaction_text,
    transcript_line_count,
    transcript_preview,
    transcript_to_text,
)
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.models.contest import Contest
from web.models.problem import Problem, ProblemSampleInteraction
from web.routes.contest_admin_problem_helpers import _html, _is_edit_allowed, _redirect
from web.routes.contest_admin_problem_judgment_urls import judgment_page_url
from web.services.problem_service import (
    get_problem_in_contest,
    load_sample_interactions,
    move_sample_interaction,
    update_sample_interaction,
)

router = APIRouter(prefix="/c/{slug}/admin/problems", tags=["contest_admin_problems"])


def build_interaction_row_views(
    request: Request,
    contest: Contest,
    interactions: list[ProblemSampleInteraction],
) -> list[SampleInteractionRowView]:
    """Adapt Web sample-interaction rows into shared list-partial view models.

    URLs are pre-built with the slug-scoped Web route names so the shared template
    never resolves module-specific ``url_for`` names.
    """
    slug = contest.login_slug
    return [
        SampleInteractionRowView(
            id=interaction.id,
            ordinal=interaction.ordinal,
            preview=transcript_preview(interaction.transcript),
            line_count=transcript_line_count(interaction.transcript),
            has_explanation=bool(interaction.explanation),
            edit_url=str(
                request.url_for(
                    "edit_problem_interaction_form",
                    slug=slug,
                    problem_id=interaction.problem_id,
                    si_id=interaction.id,
                )
            ),
            delete_url=str(
                request.url_for(
                    "problem_judgment_interaction_delete",
                    slug=slug,
                    problem_id=interaction.problem_id,
                    si_id=interaction.id,
                )
            ),
            move_url=str(
                request.url_for(
                    "move_problem_interaction",
                    slug=slug,
                    problem_id=interaction.problem_id,
                    si_id=interaction.id,
                )
            ),
        )
        for interaction in sorted(interactions, key=lambda item: item.ordinal)
    ]


async def _get_interaction(
    ctx: ContestAdminContext,
    problem_id: str,
    si_id: str,
) -> tuple[Problem, ProblemSampleInteraction] | None:
    """Load a problem and one of its sample interactions, or ``None``."""
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        return None
    interactions = await load_sample_interactions(ctx.session, problem.id, include_hidden=True)
    interaction = next((item for item in interactions if item.id == si_id), None)
    if interaction is None:
        return None
    return problem, interaction


@router.get(
    "/{problem_id}/interactions/{si_id}/edit",
    response_class=HTMLResponse,
    response_model=None,
    name="edit_problem_interaction_form",
)
async def edit_problem_interaction_form(
    request: Request,
    problem_id: str,
    si_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse | RedirectResponse:
    """Render the single sample-interaction edit page."""
    edit_url = judgment_page_url(request, ctx.contest.login_slug, problem_id, "interactions")
    found = await _get_interaction(ctx, problem_id, si_id)
    if found is None:
        flash("Sample interaction not found.", FlashCategory.DANGER)
        return _redirect(edit_url)
    problem, interaction = found

    return _html(
        request.app.state.templates.TemplateResponse(
            request,
            "admin/problems/interaction_edit.html",
            {
                "contest": ctx.contest,
                "current_user": ctx.actor,
                "problem": problem,
                "interaction": interaction,
                "is_edit": True,
                "is_edit_allowed": _is_edit_allowed(ctx.contest),
                "post_action_url": str(
                    request.url_for(
                        "update_problem_interaction",
                        slug=ctx.contest.login_slug,
                        problem_id=problem_id,
                        si_id=si_id,
                    )
                ),
                "cancel_url": edit_url,
                "transcript_value": transcript_to_text(interaction.transcript),
                "explanation_value": interaction.explanation or "",
            },
        )
    )


@router.post("/{problem_id}/interactions/{si_id}/edit", name="update_problem_interaction")
async def update_problem_interaction(
    request: Request,
    problem_id: str,
    si_id: str,
    flash: FlashDep,
    transcript: str = Form(""),
    explanation: str = Form(""),
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> RedirectResponse:
    """Save an edited sample interaction."""
    edit_url = judgment_page_url(request, ctx.contest.login_slug, problem_id, "interactions")
    if not _is_edit_allowed(ctx.contest):
        flash("Contest is not editable.", FlashCategory.DANGER)
        return _redirect(edit_url)

    found = await _get_interaction(ctx, problem_id, si_id)
    if found is None:
        flash("Sample interaction not found.", FlashCategory.DANGER)
        return _redirect(edit_url)
    _problem, interaction = found

    try:
        parsed = parse_interaction_text(transcript)
    except InteractionParseError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return _redirect(
            str(
                request.url_for(
                    "edit_problem_interaction_form",
                    slug=ctx.contest.login_slug,
                    problem_id=problem_id,
                    si_id=si_id,
                )
            )
        )

    await update_sample_interaction(interaction, transcript=parsed, explanation=explanation.strip() or None)
    await ctx.session.commit()
    flash(f"Sample interaction #{interaction.ordinal} updated.", FlashCategory.SUCCESS)
    return _redirect(editor_url(edit_url, anchor=f"si-{interaction.id}"))


@router.post(
    "/{problem_id}/interactions/{si_id}/move",
    response_class=HTMLResponse,
    response_model=None,
    name="move_problem_interaction",
)
async def move_problem_interaction(
    request: Request,
    problem_id: str,
    si_id: str,
    flash: FlashDep,
    new_ordinal: int = Query(..., ge=1, le=PG_INT32_MAX),
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse | RedirectResponse:
    """Move a sample interaction and return the refreshed list partial."""
    edit_url = judgment_page_url(request, ctx.contest.login_slug, problem_id, "interactions")
    if not _is_edit_allowed(ctx.contest):
        flash("Contest is not editable.", FlashCategory.DANGER)
        return _redirect(edit_url)

    found = await _get_interaction(ctx, problem_id, si_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Sample interaction not found.")
    problem, interaction = found

    await move_sample_interaction(ctx.session, problem, interaction, new_ordinal)
    await ctx.session.commit()

    rows = await load_sample_interactions(ctx.session, problem.id)
    return _html(
        request.app.state.templates.TemplateResponse(
            request,
            "admin/problems/interactions_table.html",
            {
                "contest": ctx.contest,
                "current_user": ctx.actor,
                "problem": problem,
                "interaction_rows": build_interaction_row_views(request, ctx.contest, rows),
                "is_edit_allowed": _is_edit_allowed(ctx.contest),
                "max_interactions": MAX_SAMPLE_INTERACTIONS,
                "validator_status": status_view(problem.custom_validator),
                "is_interactive": problem.validator_type is ProblemValidatorType.INTERACTIVE,
            },
        )
    )
