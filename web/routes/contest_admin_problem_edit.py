#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_flash import FlashCategory, FlashDep

from shared.services.contest_report_cache import invalidate_contest_report_cache
from shared.services.editor_urls import editor_url
from shared.services.form_draft import confirm_form_draft, problem_definition_draft_key
from shared.services.imageprocessing_service import ImageProcessingError
from shared.services.problem_definition_view import (
    MOVED_TO_JUDGMENT,
    TAB_EDITORIAL,
    TAB_LIMITS,
    TAB_METADATA,
    TAB_STATEMENT,
    resolve_tab,
)
from shared.services.problem_editor_save import (
    abandon_swap,
    lock_problem_row,
    open_save_swap,
)
from shared.services.problem_image import process_problem_image_upload
from shared.services.problem_package import DEFAULT_OUTPUT_LIMIT_BYTES, MAX_TITLE_CHARS
from shared.services.problem_package.edit_swap import EditArtifactSwap, commit_with_edit_swap
from web.config import settings
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.models.language import Language
from web.models.problem import Problem
from web.routes.contest_admin_problem_edit_render import build_editor_context
from web.routes.contest_admin_problem_helpers import (
    _html,
    _is_edit_allowed,
    _is_limits_edit_allowed,
    _redirect,
)
from web.routes.contest_admin_problem_judgment_urls import judgment_page_url
from web.routes.contest_admin_problem_limits_helpers import _validate_language_limit_inputs
from web.routes.contest_admin_problem_view import contest_editor_tabs
from web.services.category_service import get_or_create_categories, replace_problem_categories
from web.services.problem_service import (
    changed_effective_limits,
    create_problem_limit_change_batch,
    get_contest_languages,
    get_language_limits_map,
    get_md_statement_path,
    get_problem_definition_in_contest,
    get_statement_path,
    problem_fallback_limits,
    submitted_language_limits,
    upsert_language_limits,
    validate_md_content,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/c/{slug}/admin/problems", tags=["contest_admin_problems"])


@router.get(
    "/{problem_id}/edit",
    response_class=HTMLResponse,
    response_model=None,
    name="edit_problem_form",
)
async def edit_problem_form(
    request: Request,
    problem_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse | RedirectResponse:
    """Render the problem definition editor.

    A ``?tab=`` naming a pane that moved to the judgment-data editor is redirected
    there rather than falling back to Metadata: an old link or bookmark asking for
    test cases should land on test cases.
    """
    templates = request.app.state.templates
    problem = await get_problem_definition_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")

    requested = request.query_params.get("tab")
    moved = MOVED_TO_JUDGMENT.get(requested or "")
    if moved is not None:
        return _redirect(judgment_page_url(request, ctx.contest.login_slug, problem_id, moved))

    context = await build_editor_context(
        request,
        ctx,
        problem,
        active_tab=requested,
    )
    return _html(templates.TemplateResponse(request, "admin/problems/edit.html", context))


@router.post("/{problem_id}/edit", response_class=HTMLResponse, response_model=None, name="edit_problem_submit")
async def edit_problem_submit(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    title: str = Form(""),
    color: str = Form("#000000"),
    author: str = Form(""),
    notes: str = Form(""),
    time_limit_ms: str = Form(""),
    memory_limit_kb: str = Form(""),
    pids_limit: str = Form(""),
    output_limit_in_bytes: str = Form(""),
    active_tab: str = Form(""),
    category_names: str = Form(""),
    statement_file: UploadFile = File(None),
    statement_source: str = Form("unchanged"),
    md_content: str = Form(""),
    editorial: str = Form(""),
    image: UploadFile = File(None),
    image_caption: str = Form(""),
    clear_image: bool = Form(False),
) -> HTMLResponse | RedirectResponse:
    """Save what the problem *is*: metadata, statement, editorial, categories, limits.

    What judging runs against -- test cases, the validator, sample interactions --
    is edited on its own pages and applies as it is clicked. This Save briefly
    carried all of it, which meant one form held a problem's entire test data and
    every combination of pending decisions about it had to be reconciled.

    The statement is still a file, so this still commits rows and files together
    through the artifact swap.
    """
    problem = await get_problem_definition_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")

    templates = request.app.state.templates
    form = await request.form()
    slug = ctx.contest.login_slug

    # The edit form renders the strategy as a read-only badge and submits no
    # field for it, so a value arriving here can only be tampering. Reject it
    # rather than ignoring it, and reject an unknown value the same way.
    submitted_strategy = form.get("validator_type")
    if submitted_strategy is not None and str(submitted_strategy) != problem.validator_type.value:
        flash(
            "A problem's validation strategy is immutable and cannot be changed on edit.",
            FlashCategory.DANGER,
        )
        return _redirect(str(request.url_for("edit_problem_form", slug=slug, problem_id=problem_id)))

    # Serialize against another Save of the same problem *before* reading its test
    # cases: two Saves that both snapshot the live directory would each stage a
    # complete replacement, and the loser would quietly reinstate what the winner
    # replaced.
    if not await lock_problem_row(ctx.session, "contest", problem.id):
        raise Exception("Problem not found")

    languages = await get_contest_languages(ctx.session, ctx.contest)
    existing_limits = await get_language_limits_map(ctx.session, problem)
    language_limit_errors = _validate_language_limit_inputs(languages, form)

    if not _is_edit_allowed(ctx.contest) and _is_limits_edit_allowed(ctx.contest) and active_tab == TAB_LIMITS:
        return await _save_limits_only(
            request,
            ctx,
            problem,
            flash=flash,
            form=form,
            languages=languages,
            existing_limits=existing_limits,
            errors=language_limit_errors,
        )

    errors: list[str] = []
    field_errors: dict[str, str] = {}
    if not _is_edit_allowed(ctx.contest):
        errors.append("Contest is not editable.")
    errors.extend(language_limit_errors)

    title = title.strip()
    if not title:
        _add_field_error(errors, field_errors, "title", "Title is required.")
    elif len(title) > MAX_TITLE_CHARS:
        _add_field_error(
            errors,
            field_errors,
            "title",
            f"Title must be {MAX_TITLE_CHARS} characters or fewer.",
        )

    tlms = _positive_int(time_limit_ms, "Time limit (ms)", "time_limit_ms", errors, field_errors)
    mlkb = _positive_int(memory_limit_kb, "Memory limit (KB)", "memory_limit_kb", errors, field_errors)
    pids_limit_value = _positive_int(pids_limit, "PIDs limit", "pids_limit", errors, field_errors)
    output_limit_value = DEFAULT_OUTPUT_LIMIT_BYTES
    if output_limit_in_bytes.strip():
        parsed_output_limit = _positive_int(
            output_limit_in_bytes,
            "Output limit",
            "output_limit_in_bytes",
            errors,
            field_errors,
        )
        if parsed_output_limit is not None:
            output_limit_value = parsed_output_limit

    statement_dir = settings.PROBLEM_STATEMENT_DIR
    existing_has_md = await anyio.to_thread.run_sync(lambda: get_md_statement_path(problem.id, statement_dir).exists())
    result_is_md = existing_has_md
    pdf_bytes: bytes | None = None

    if statement_source == "pdf":
        result_is_md = False
        if not statement_file or not statement_file.filename:
            errors.append("A PDF statement file is required.")
        elif not statement_file.filename.lower().endswith(".pdf"):
            errors.append("Statement file must have .pdf extension.")
        else:
            pdf_bytes = await statement_file.read()
    elif statement_source == "md":
        result_is_md = True
        if not md_content.strip():
            _add_field_error(
                errors,
                field_errors,
                "md_content",
                "Markdown statement cannot be empty.",
            )
        else:
            markdown_errors = validate_md_content(md_content)
            errors.extend(markdown_errors)
            if markdown_errors:
                field_errors["md_content"] = markdown_errors[0]
    elif statement_source == "unchanged":
        result_is_md = existing_has_md
    else:
        errors.append("Invalid statement source.")

    if editorial.strip():
        editorial_errors = validate_md_content(editorial)
        if editorial_errors:
            _add_field_error(
                errors,
                field_errors,
                "editorial",
                f"Editorial: {editorial_errors[0]}",
            )

    image_b64: str | None = None
    image_mime: str | None = None
    if image and image.filename:
        try:
            image_b64, image_mime = await process_problem_image_upload(request.app.state.image_service, image)
        except (ImageProcessingError, ValueError) as exc:
            errors.append(f"Problem image: {exc}")

    if errors:
        context = await build_editor_context(
            request,
            ctx,
            problem,
            active_tab=_validation_tab(field_errors, language_limit_errors, errors, active_tab),
            errors=errors,
            field_errors=field_errors,
            form_data={
                "title": title,
                "color": color,
                "author": author,
                "notes": notes,
                "time_limit_ms": time_limit_ms,
                "memory_limit_kb": memory_limit_kb,
                "pids_limit": pids_limit,
                "output_limit_in_bytes": output_limit_in_bytes,
                "image_caption": image_caption,
            },
            md_content=md_content,
            editorial_content=editorial,
            has_pdf=statement_source == "unchanged" and not existing_has_md,
            has_md=(statement_source == "unchanged" and existing_has_md) or statement_source == "md",
            category_names_csv=category_names,
            # The illustration is the one file this form still carries, and a
            # browser cannot re-attach it after a rejection.
            reselect_uploads=("the problem illustration",) if image and image.filename else (),
        )
        return _html(templates.TemplateResponse(request, "admin/problems/edit.html", context, status_code=422))

    assert tlms is not None
    assert mlkb is not None
    assert pids_limit_value is not None
    problem.title = title
    problem.color = color
    problem.author = author.strip() or None
    problem.notes = notes.strip() or None
    problem.editorial = editorial if editorial.strip() else None
    problem.time_limit_ms = tlms
    problem.memory_limit_kb = mlkb
    problem.pids_limit = pids_limit_value
    problem.output_limit_in_bytes = output_limit_value

    # A new upload wins over the remove checkbox. Removing the image removes its
    # caption too: a caption with no image to caption is meaningless.
    if clear_image and not image_b64:
        problem.problem_image_base64 = None
        problem.problem_image_mime = None
        problem.problem_image_caption = None
    else:
        if image_b64:
            problem.problem_image_base64 = image_b64
            problem.problem_image_mime = image_mime
        problem.problem_image_caption = image_caption.strip() or None

    category_names_list = [name.strip() for name in category_names.split(",") if name.strip()]
    categories = await get_or_create_categories(ctx.session, category_names_list)
    await replace_problem_categories(ctx.session, problem, categories)

    lang_limits = submitted_language_limits(languages, form, existing_limits)
    await upsert_language_limits(ctx.session, problem, lang_limits)

    # The statement is a file, so the rows and the file still commit together.
    swap = await open_save_swap(
        ctx.session,
        domain="contest",
        problem_id=problem.id,
        testcase_dir=settings.PROBLEM_TESTCASE_DIR,
    )
    try:
        _stage_statement(swap, problem.id, statement_source, pdf_bytes, md_content, statement_dir)
    except Exception as exc:  # staged nothing durable yet; nothing to restore
        logger.exception("problem editor: staging failed for problem %s", problem.id)
        await abandon_swap(ctx.session, swap)
        flash(f"Could not save the problem: {exc}", FlashCategory.DANGER)
        return _redirect(str(request.url_for("edit_problem_form", slug=slug, problem_id=problem_id)))

    await commit_with_edit_swap(ctx.session, swap)
    await invalidate_contest_report_cache(getattr(request.app.state, "valkey_runtime", None), str(ctx.contest.id))
    confirm_form_draft(request, problem_definition_draft_key("web", contest_id=slug, problem_id=problem.id))

    flash("Changes saved successfully.", FlashCategory.SUCCESS)
    if result_is_md:
        # Markdown is edited in place, so the author stays on the pane they were on.
        return _redirect(
            editor_url(
                str(request.url_for("edit_problem_form", slug=slug, problem_id=problem_id)),
                tab=resolve_tab(active_tab or None, allowed=frozenset(contest_editor_tabs(problem.validator_type))),
            )
        )
    return _redirect(str(request.url_for("manage_problems", slug=slug)))


def _add_field_error(
    errors: list[str],
    field_errors: dict[str, str],
    field: str,
    message: str,
) -> None:
    """Record one server error both globally and beside its form control."""
    errors.append(message)
    field_errors.setdefault(field, message)


def _positive_int(
    raw: str,
    label: str,
    field: str,
    errors: list[str],
    field_errors: dict[str, str],
) -> int | None:
    """Parse one required positive integer field, collecting its error message."""
    if not raw.strip():
        _add_field_error(errors, field_errors, field, f"{label} is required.")
        return None
    try:
        value = int(raw)
    except ValueError:
        _add_field_error(errors, field_errors, field, f"{label} must be a positive integer.")
        return None
    if value < 1:
        _add_field_error(errors, field_errors, field, f"{label} must be >= 1.")
        return None
    return value


def _validation_tab(
    field_errors: Mapping[str, str],
    language_limit_errors: list[str],
    errors: list[str],
    submitted_tab: str,
) -> str | None:
    """Open the pane containing the first actionable server-side error."""
    first_field = next(iter(field_errors), "")
    if first_field == "title":
        return TAB_METADATA
    if first_field == "md_content":
        return TAB_STATEMENT
    if first_field == "editorial":
        return TAB_EDITORIAL
    if first_field in {"time_limit_ms", "memory_limit_kb", "pids_limit", "output_limit_in_bytes"}:
        return TAB_LIMITS
    if language_limit_errors:
        return TAB_LIMITS
    if any("statement" in error.lower() or "problem image" in error.lower() for error in errors):
        return TAB_STATEMENT
    return submitted_tab or None


def _stage_statement(
    swap: EditArtifactSwap,
    problem_id: str,
    statement_source: str,
    pdf_bytes: bytes | None,
    md_content: str,
    statement_dir: Path,
) -> None:
    """Stage the chosen statement and the reversible removal of the other format.

    Dropping the format the author switched away from is as much part of the Save
    as writing the new one, so it goes through the swap too: a failed commit must
    leave the problem with the statement it had, not with neither.
    """
    pdf_path = get_statement_path(problem_id, statement_dir)
    md_path = get_md_statement_path(problem_id, statement_dir)
    if statement_source == "pdf" and pdf_bytes is not None:
        swap.stage_file(pdf_bytes, pdf_path, statement_dir)
        swap.stage_removal(md_path, statement_dir)
    elif statement_source == "md":
        swap.stage_file(md_content.encode("utf-8"), md_path, statement_dir)
        swap.stage_removal(pdf_path, statement_dir)


async def _save_limits_only(
    request: Request,
    ctx: ContestAdminContext,
    problem: Problem,
    *,
    flash: FlashDep,
    form: Mapping[str, Any],
    languages: list[Language],
    existing_limits: dict[str, Any],
    errors: list[str],
) -> HTMLResponse | RedirectResponse:
    """Save the Limits tab alone, which a running contest still permits.

    This branch writes no filesystem artifact, so it commits on its own rather
    than through the artifact swap: there is nothing to stage, and opening a swap
    to promote nothing would only add a journal to clean up.
    """
    templates = request.app.state.templates
    if errors:
        context = await build_editor_context(request, ctx, problem, active_tab=TAB_LIMITS, errors=errors)
        return _html(templates.TemplateResponse(request, "admin/problems/edit.html", context, status_code=422))

    before_fallback = problem_fallback_limits(problem)
    lang_limits = submitted_language_limits(languages, form, existing_limits)
    changed_limits = changed_effective_limits(
        problem,
        languages,
        before_overrides=existing_limits,
        after_overrides=lang_limits,
        before_fallback=before_fallback,
        after_fallback=before_fallback,
    )
    await upsert_language_limits(ctx.session, problem, lang_limits)
    batch = await create_problem_limit_change_batch(ctx.session, ctx.contest, problem, ctx.actor, changed_limits)
    await ctx.session.commit()
    slug = ctx.contest.login_slug
    confirm_form_draft(request, problem_definition_draft_key("web", contest_id=slug, problem_id=problem.id))
    if batch is not None:
        flash("Limits saved successfully. Review the affected submissions batch.", FlashCategory.SUCCESS)
        return _redirect(
            str(
                request.url_for(
                    "problem_limit_change_batch_review",
                    slug=slug,
                    problem_id=problem.id,
                    batch_id=batch.id,
                )
            )
        )
    flash("Limits saved successfully. No affected submissions were found.", FlashCategory.SUCCESS)
    return _redirect(
        editor_url(str(request.url_for("edit_problem_form", slug=slug, problem_id=problem.id)), tab=TAB_LIMITS)
    )
