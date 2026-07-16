#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest admin routes for a problem's sample interactions.

An interactive problem has no public test cases: what contestants see instead are
these authored conversations. The problem edit page manages them the way it
manages test cases — pending add rows and pending removals ride the single Save
(see :func:`apply_pending_interactions`) — while per-row edit and drag-reorder get
their own endpoints here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from shared.services.custom_validator import status_view
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
from web.services.problem_service import (
    append_sample_interaction,
    get_problem_in_contest,
    load_sample_interactions,
    move_sample_interaction,
    remove_sample_interaction_and_resequence,
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


def parse_pending_interactions(
    form_data: Mapping[str, Any],
) -> list[tuple[dict[str, object], str | None]]:
    """Parse the edit form's inline add-rows without touching the database.

    Split from :func:`apply_pending_interactions` so the caller can reject a
    malformed transcript *before* it starts mutating the problem — the Contest
    edit route deletes test-case files ahead of its commit, so a late failure
    there could not be cleanly undone.

    Raises:
        InteractionParseError: If a row's transcript is malformed.
    """
    add_indices = sorted(
        {
            int(key.rsplit("_", 1)[1])
            for key in form_data
            if key.startswith("si_transcript_") and key.rsplit("_", 1)[1].isdigit()
        }
    )
    parsed: list[tuple[dict[str, object], str | None]] = []
    for index in add_indices:
        raw = str(form_data.get(f"si_transcript_{index}", ""))
        # Blank rows are ones the author added and left empty; skip them. The parser
        # gets the *unstripped* text, because a protocol line's trailing spaces are
        # part of what the program wrote and must survive verbatim.
        if not raw.strip():
            continue
        parsed.append(
            (
                parse_interaction_text(raw),
                str(form_data.get(f"si_explanation_{index}", "")).strip() or None,
            )
        )
    return parsed


def pending_interaction_removal_ids(form_data: Mapping[str, Any]) -> set[str]:
    """Return the interaction ids the user marked for removal on the edit page."""
    raw = str(form_data.get("si_remove_ids", "") or "")
    return {value.strip() for value in raw.split(",") if value.strip()}


async def apply_pending_interactions(
    session: AsyncSession,
    problem: Problem,
    form_data: Mapping[str, Any],
    additions: list[tuple[dict[str, object], str | None]],
) -> None:
    """Apply the interaction removals and additions the edit form deferred to Save.

    Removals run **before** additions, so an author who marks one of five
    interactions for removal can add its replacement in the same edit. The cap is
    therefore judged against the row count the save actually produces, not the one
    the problem started with, and it is checked up front so a save that cannot fit
    fails before it has mutated anything.

    Args:
        session: Open session; nothing is committed here.
        problem: The problem being saved.
        form_data: Raw submitted form (read for the pending-removal ids).
        additions: Already-parsed rows from :func:`parse_pending_interactions`.

    Raises:
        ValueError: If the save's final row count would exceed the cap.
    """
    remove_ids = pending_interaction_removal_ids(form_data)
    existing = await load_sample_interactions(session, problem.id, include_hidden=True)
    to_remove = [item for item in existing if item.id in remove_ids]

    final_count = len(existing) - len(to_remove) + len(additions)
    if final_count > MAX_SAMPLE_INTERACTIONS:
        raise ValueError(
            f"A problem may have at most {MAX_SAMPLE_INTERACTIONS} sample interactions; "
            f"this save would leave {final_count}."
        )

    for interaction in sorted(to_remove, key=lambda item: item.ordinal, reverse=True):
        await remove_sample_interaction_and_resequence(session, problem, interaction)

    for transcript, explanation in additions:
        await append_sample_interaction(session, problem, transcript=transcript, explanation=explanation)


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
    edit_url = str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id))
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
    edit_url = str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id))
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
    return _redirect(edit_url)


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
    new_ordinal: int = Query(..., ge=1),
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse | RedirectResponse:
    """Move a sample interaction and return the refreshed list partial."""
    edit_url = str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id))
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
            },
        )
    )
