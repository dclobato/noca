#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Contest judgment-data validator and sample-interaction pages.

Both are strategy-gated: a standard problem has neither a validator to configure
nor interactions to author, so its judgment data is test cases alone and these
pages redirect rather than offering something that can never run.

Their mutations change database rows only -- validator source lives in table
columns, and a transcript is text -- so they commit directly instead of opening
an artifact swap. The row lock still applies, so they cannot interleave with a
test-case action that is staging the problem's directory.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from shared.enumerations import ProblemValidatorType
from shared.services.interaction_pending_ops import (
    SubmittedInteractionError,
    SubmittedInteractionRow,
    parse_pending_interactions,
    submitted_interaction_rows,
)
from shared.services.problem_editor_save import lock_problem_row
from shared.services.public_export_generation import bump_public_export_generation
from shared.services.sample_interactions import MAX_SAMPLE_INTERACTIONS
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.routes.contest_admin_problem_helpers import _html, _is_edit_allowed, _redirect
from web.routes.contest_admin_problem_judgment_urls import judgment_page_url
from web.routes.contest_admin_problem_judgment_view import build_judgment_context
from web.services.problem_service import (
    append_sample_interaction,
    get_problem_in_contest,
    load_sample_interactions,
    remove_sample_interaction_and_resequence,
)

router = APIRouter(prefix="/c/{slug}/admin/problems", tags=["contest_admin_problems"])


@router.get(
    "/{problem_id}/judgment/validator",
    response_class=HTMLResponse,
    response_model=None,
    name="problem_judgment_validator",
)
async def problem_judgment_validator(
    request: Request,
    problem_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse | RedirectResponse:
    """Render the validator page, for an interactive problem."""
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")
    if problem.validator_type is not ProblemValidatorType.INTERACTIVE:
        return _redirect(judgment_page_url(request, ctx.contest.login_slug, problem_id, "test-cases"))

    templates = request.app.state.templates
    context = await build_judgment_context(request, ctx, problem_id, active_page="validator")
    return _html(templates.TemplateResponse(request, "admin/problems/judgment_validator.html", context))


@router.get(
    "/{problem_id}/judgment/interactions",
    response_class=HTMLResponse,
    response_model=None,
    name="problem_judgment_interactions",
)
async def problem_judgment_interactions(
    request: Request,
    problem_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse | RedirectResponse:
    """Render the sample-interactions page, for an interactive problem."""
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")
    if problem.validator_type is not ProblemValidatorType.INTERACTIVE:
        return _redirect(judgment_page_url(request, ctx.contest.login_slug, problem_id, "test-cases"))

    templates = request.app.state.templates
    context = await build_judgment_context(request, ctx, problem_id, active_page="interactions")
    return _html(templates.TemplateResponse(request, "admin/problems/judgment_interactions.html", context))


@router.post("/{problem_id}/judgment/interactions", name="problem_judgment_interactions_save")
async def problem_judgment_interactions_save(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> Response:
    """Add the transcripts typed inline on the page."""
    page_url = judgment_page_url(request, ctx.contest.login_slug, problem_id, "interactions")
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None or problem.validator_type is not ProblemValidatorType.INTERACTIVE:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(page_url)
    if not _is_edit_allowed(ctx.contest):
        flash("Contest is not editable.", FlashCategory.DANGER)
        return _redirect(page_url)

    form = await request.form()
    submitted = submitted_interaction_rows(form)
    try:
        pending = parse_pending_interactions(form)
    except SubmittedInteractionError as exc:
        errored = tuple(replace(row, error=str(exc)) if row.index == exc.index else row for row in submitted)
        return await _render_rejected_interactions(
            request,
            ctx,
            problem_id,
            errored,
            "Correct the highlighted interaction and save again.",
        )
    if not pending:
        return await _render_rejected_interactions(
            request,
            ctx,
            problem_id,
            submitted,
            "Nothing to add: write a transcript first.",
        )

    existing = await load_sample_interactions(ctx.session, problem.id)
    if len(existing) + len(pending) > MAX_SAMPLE_INTERACTIONS:
        message = f"A problem may have at most {MAX_SAMPLE_INTERACTIONS} sample interactions."
        last_index = pending[-1].index
        errored = tuple(replace(row, error=message) if row.index == last_index else row for row in submitted)
        return await _render_rejected_interactions(
            request,
            ctx,
            problem_id,
            errored,
            "Remove a submitted row or an existing interaction before saving.",
        )

    if not await lock_problem_row(ctx.session, "contest", problem.id):
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(page_url)
    for item in pending:
        try:
            await append_sample_interaction(
                ctx.session,
                problem,
                transcript=item.transcript,
                explanation=item.explanation,
            )
        except ValueError as exc:
            await ctx.session.rollback()
            errored = tuple(replace(row, error=str(exc)) if row.index == item.index else row for row in submitted)
            return await _render_rejected_interactions(
                request,
                ctx,
                problem_id,
                errored,
                "Correct the highlighted interaction and save again.",
            )
    # After the loop: a rejected item rolls the transaction back above, which
    # would discard a bump made before it.
    await bump_public_export_generation(ctx.session, "contest", problem.id)
    await ctx.session.commit()

    flash(f"{len(pending)} sample interaction(s) added.", FlashCategory.SUCCESS)
    return _redirect(page_url)


async def _render_rejected_interactions(
    request: Request,
    ctx: ContestAdminContext,
    problem_id: str,
    rows: tuple[SubmittedInteractionRow, ...],
    message: str,
) -> HTMLResponse:
    """Render submitted interaction rows beside their validation error."""
    context = await build_judgment_context(request, ctx, problem_id, active_page="interactions")
    context |= {
        "submitted_interaction_rows": rows,
        "pending_error": message,
        "pending_next_index": max((row.index for row in rows), default=-1) + 1,
    }
    templates = request.app.state.templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/judgment_interactions.html",
            context,
            status_code=422,
        )
    )


@router.post("/{problem_id}/judgment/interactions/{si_id}/delete", name="problem_judgment_interaction_delete")
async def problem_judgment_interaction_delete(
    request: Request,
    problem_id: str,
    si_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> RedirectResponse:
    """Delete one sample interaction immediately."""
    page_url = judgment_page_url(request, ctx.contest.login_slug, problem_id, "interactions")
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(page_url)
    if not _is_edit_allowed(ctx.contest):
        flash("Contest is not editable.", FlashCategory.DANGER)
        return _redirect(page_url)

    if not await lock_problem_row(ctx.session, "contest", problem.id):
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(page_url)
    interactions = await load_sample_interactions(ctx.session, problem.id)
    interaction = next((item for item in interactions if item.id == si_id), None)
    if interaction is None:
        flash("Sample interaction not found.", FlashCategory.DANGER)
        return _redirect(page_url)
    await remove_sample_interaction_and_resequence(ctx.session, problem, interaction)
    # Sample interactions are public package members.
    await bump_public_export_generation(ctx.session, "contest", problem.id)
    await ctx.session.commit()

    flash("Sample interaction removed.", FlashCategory.SUCCESS)
    return _redirect(page_url)
