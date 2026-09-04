#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Contest judgment-data page's custom-validator endpoints.

Uploading and removing a validator are immediate actions on their dedicated
page. Source download/view and the status fragment expose the current revision.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_flash import FlashCategory, FlashDep

from shared.enumerations import ProblemValidatorType
from shared.language_configs import default_extension_for_language
from shared.language_registry import highlightjs_language_for_language_id
from shared.services.custom_validator import (
    build_validation_job,
    current_validator_source,
    parse_validator_source,
    remove_validator,
    stage_candidate,
    status_view,
)
from shared.services.public_export_generation import bump_public_export_generation
from shared.services.valkey_service import enqueue_custom_validator_validation_job
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.models.problem import ProblemCustomValidator
from web.routes.contest_admin_problem_helpers import _html, _is_edit_allowed, _label
from web.routes.contest_admin_problem_judgment_urls import judgment_page_url
from web.services.problem_service import (
    convert_sample_test_cases_to_secret,
    delete_sample_interactions,
    get_active_languages,
    get_problem_in_contest,
    hide_sample_interactions,
    unhide_sample_interactions,
)
from web.services.user_read_rate_limit import web_user_read_rate_limit

router = APIRouter(
    prefix="/c/{slug}/admin/problems", tags=["contest_admin_problems"], dependencies=[Depends(web_user_read_rate_limit)]
)


@router.post("/{problem_id}/validator", name="upload_problem_custom_validator")
async def upload_problem_custom_validator(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    language_id: str = Form(...),
    source_file: UploadFile = File(...),
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> RedirectResponse:
    """Stage and enqueue a Contest validator without saving unrelated edits."""
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        flash("Problem not found.", FlashCategory.DANGER)
        return RedirectResponse(request.url_for("manage_problems", slug=ctx.contest.login_slug), 303)
    # Back to the page that owns the validator, not the test cases: an upload that
    # was refused has to be corrected here, and one that was accepted is watched
    # here while it compiles.
    edit_url = judgment_page_url(request, ctx.contest.login_slug, problem.id, "validator")
    # A running contest's judgment data is frozen: the pages hide these actions,
    # but hiding a control is not a permission check, and replacing the validator
    # a live contest is being judged by changes verdicts under contestants.
    if not _is_edit_allowed(ctx.contest):
        flash("Contest is not editable.", FlashCategory.DANGER)
        return RedirectResponse(edit_url, 303)
    # Only an interactive problem has anywhere to put a validator. This reads the
    # stored strategy, not the current source, so a problem whose source was
    # removed can still upload a replacement -- the documented recovery path.
    if problem.validator_type is not ProblemValidatorType.INTERACTIVE:
        flash("Only an interactive problem can have a custom validator.", FlashCategory.DANGER)
        return RedirectResponse(edit_url, 303)
    active_language_ids = {language.id for language in await get_active_languages(ctx.session)}
    if language_id not in active_language_ids:
        flash("Validator language is not active.", FlashCategory.DANGER)
        return RedirectResponse(edit_url, 303)
    try:
        source = parse_validator_source(await source_file.read())
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return RedirectResponse(edit_url, 303)
    validator = problem.custom_validator
    if validator is None:
        validator = ProblemCustomValidator(problem_id=problem.id)
        ctx.session.add(validator)
    token = stage_candidate(validator, language_id=language_id, source=source)

    # The problem is interactive from now on: it presents sample interactions, so
    # any public test case becomes secret, and interactions hidden by an earlier
    # validator removal resurface.
    demoted = await convert_sample_test_cases_to_secret(ctx.session, problem.id)
    resurfaced = await unhide_sample_interactions(ctx.session, problem.id)
    # Staging a validator demotes public cases and resurfaces interactions,
    # both of which the public package ships.
    await bump_public_export_generation(ctx.session, "contest", problem.id)
    await ctx.session.commit()

    job = build_validation_job(domain="contest", problem_id=problem.id, candidate_token=token)
    await enqueue_custom_validator_validation_job(request.app.state.valkey_runtime, job)
    flash("Custom validator queued for compilation.", FlashCategory.SUCCESS)
    if demoted:
        flash(
            f"{demoted} sample test case(s) became secret: interactive problems show sample interactions instead.",
            FlashCategory.WARNING,
        )
    if resurfaced:
        flash(f"{resurfaced} previously hidden sample interaction(s) are visible again.", FlashCategory.INFO)
    return RedirectResponse(edit_url, 303)


@router.get("/{problem_id}/validator/source", name="download_problem_custom_validator")
async def download_problem_custom_validator(
    problem_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> Response:
    """Download the current validator source (active revision, else candidate)."""
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None or problem.custom_validator is None:
        raise HTTPException(404, "This problem has no custom validator.")
    validator_source = current_validator_source(problem.custom_validator)
    if validator_source is None:
        raise HTTPException(404, "This problem has no custom validator.")
    filename = f"validator-{problem.ordinal}{default_extension_for_language(validator_source.language_id)}"
    return Response(
        content=validator_source.source.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{problem_id}/validator/source/view",
    response_class=HTMLResponse,
    name="view_problem_custom_validator_source",
)
async def view_problem_custom_validator_source(
    request: Request,
    problem_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    """Render the current validator source with syntax highlighting."""
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None or problem.custom_validator is None:
        raise HTTPException(404, "This problem has no custom validator.")
    validator_source = current_validator_source(problem.custom_validator)
    if validator_source is None:
        raise HTTPException(404, "This problem has no custom validator.")
    highlight_language = highlightjs_language_for_language_id(validator_source.language_id)
    return _html(
        request.app.state.templates.TemplateResponse(
            request,
            "admin/problems/validator_source.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "problem": problem,
                "problem_label": _label(problem.ordinal),
                "source_code": validator_source.source,
                "highlight_language": highlight_language,
                "highlight_language_class": f"language-{highlight_language}",
                "highlight_theme_path": "highlight/styles/github.min.css",
                "highlight_core_path": "highlight/highlight.min.js",
                "highlight_language_path": (
                    None if highlight_language == "plaintext" else f"highlight/languages/{highlight_language}.min.js"
                ),
                "highlight_line_numbers_path": "highlight/plugins/highlightjs-line-numbers.min.js",
            },
        )
    )


@router.get("/{problem_id}/validator/status", response_class=HTMLResponse, name="problem_custom_validator_status")
async def problem_custom_validator_status(
    request: Request,
    problem_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    """Render the HTMX-polled Contest validator status partial."""
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise ValueError("Problem not found")
    return _html(
        request.app.state.templates.TemplateResponse(
            request,
            "admin/problems/_validator_status.html",
            {
                "contest": ctx.contest,
                "problem": problem,
                "validator_status": status_view(problem.custom_validator),
                "is_interactive": problem.validator_type is ProblemValidatorType.INTERACTIVE,
            },
        )
    )


@router.post("/{problem_id}/validator/remove", name="remove_problem_custom_validator")
async def remove_problem_custom_validator_route(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    keep_interactions: Literal["true", "false"] = Form(...),
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> RedirectResponse:
    """Clear both revisions; stale queued jobs become token mismatches.

    Without its validator the problem's sample interactions have nothing to
    illustrate, so the caller must state what happens to them. ``keep_interactions``
    is deliberately an exact ``"true"``/``"false"`` string rather than a ``bool``:
    FastAPI would coerce ``1``, ``on`` and ``yes`` too, and the choice between
    hiding data and destroying it must not hinge on a spelling.
    """
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise ValueError("Problem not found")
    # Back to the page that owns the validator, not the test cases: an upload that
    # was refused has to be corrected here, and one that was accepted is watched
    # here while it compiles.
    edit_url = judgment_page_url(request, ctx.contest.login_slug, problem.id, "validator")
    # A running contest's judgment data is frozen: the pages hide these actions,
    # but hiding a control is not a permission check, and replacing the validator
    # a live contest is being judged by changes verdicts under contestants.
    if not _is_edit_allowed(ctx.contest):
        flash("Contest is not editable.", FlashCategory.DANGER)
        return RedirectResponse(edit_url, 303)
    if problem.custom_validator is None:
        return RedirectResponse(edit_url, 303)

    keep = keep_interactions == "true"
    affected = (
        await hide_sample_interactions(ctx.session, problem.id)
        if keep
        else await delete_sample_interactions(ctx.session, problem.id)
    )
    remove_validator(problem.custom_validator)
    await ctx.session.delete(problem.custom_validator)
    # Removing the validator hides or deletes the interactions the public
    # package ships.
    await bump_public_export_generation(ctx.session, "contest", problem.id)
    await ctx.session.commit()

    flash("Custom validator removed.", FlashCategory.SUCCESS)
    if affected:
        flash(
            f"{affected} sample interaction(s) were "
            + ("hidden; they will resurface if you add a validator again." if keep else "permanently deleted."),
            FlashCategory.INFO if keep else FlashCategory.WARNING,
        )
    return RedirectResponse(edit_url, 303)
