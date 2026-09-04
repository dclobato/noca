#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import logging

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_flash import FlashCategory, FlashDep

from shared.enumerations import ProblemValidatorType
from shared.http_params import PG_INT32_MAX
from shared.services.admin_audit import record_admin_action
from shared.services.form_draft import confirm_form_draft, problem_definition_draft_key
from shared.services.imageprocessing_service import ImageProcessingError
from shared.services.problem_editor_save import abandon_swap, open_save_swap, stage_test_cases
from shared.services.problem_export_cache import discard_cached_export, export_cache_dir
from shared.services.problem_image import process_problem_image_upload
from shared.services.problem_package import DEFAULT_OUTPUT_LIMIT_BYTES, MAX_TITLE_CHARS
from shared.services.problem_package.edit_swap import commit_with_edit_swap
from web.config import settings
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.models.problem import Problem
from web.routes import contest_admin_problem_edit as _contest_admin_problem_edit
from web.routes import contest_admin_problem_limits as _contest_admin_problem_limits
from web.routes.contest_admin_problem_edit_render import editor_notices
from web.routes.contest_admin_problem_helpers import (
    _html,
    _is_edit_allowed,
    _is_limits_edit_allowed,
    _is_remove_allowed,
    _label,
    _redirect,
    _remove_blocked_reason,
)
from web.routes.contest_admin_problem_judgment_urls import judgment_page_url
from web.routes.contest_admin_problem_limits_helpers import _validate_language_limit_inputs
from web.routes.contest_admin_problem_new import resolve_choice_or_redirect
from web.routes.contest_admin_problem_view import build_problem_form_view
from web.services.category_service import get_or_create_categories, replace_problem_categories
from web.services.problem_service import (
    BALLOON_COLORS,
    append_problem,
    delete_all_testcase_files,
    delete_problem_statement,
    get_contest_languages,
    get_contest_problems,
    get_problem_in_contest,
    move_problem,
    remove_problem_and_resequence,
    submitted_language_limits,
    upsert_language_limits,
    validate_md_content,
)
from web.services.problem_service.files import get_md_statement_path, get_statement_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/c/{slug}/admin/problems", tags=["contest_admin_problems"])

edit_problem_form = _contest_admin_problem_edit.edit_problem_form
edit_problem_submit = _contest_admin_problem_edit.edit_problem_submit
enqueue_problem_profiling = _contest_admin_problem_limits.enqueue_problem_profiling
problem_profiling_status_partial = _contest_admin_problem_limits.problem_profiling_status_partial
apply_problem_fallback_limits_route = _contest_admin_problem_limits.apply_problem_fallback_limits_route
problem_limit_change_batch_review = _contest_admin_problem_limits.problem_limit_change_batch_review
problem_limit_change_batch_rejudge_all = _contest_admin_problem_limits.problem_limit_change_batch_rejudge_all
problem_limit_change_batch_rejudge_language = _contest_admin_problem_limits.problem_limit_change_batch_rejudge_language

__all__ = [
    "router",
    "edit_problem_form",
    "edit_problem_submit",
    "enqueue_problem_profiling",
    "problem_profiling_status_partial",
    "apply_problem_fallback_limits_route",
    "problem_limit_change_batch_review",
    "problem_limit_change_batch_rejudge_all",
    "problem_limit_change_batch_rejudge_language",
]


# ---------------------------------------------------------------------------
# Browse / list
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse, name="manage_problems")
async def manage_problems(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    problems = await get_contest_problems(ctx.session, ctx.contest)
    problem_rows = [(p, _label(p.ordinal), len(p.test_cases)) for p in problems]
    is_edit = _is_edit_allowed(ctx.contest)
    is_remove = _is_remove_allowed(ctx.contest)
    reason = _remove_blocked_reason(ctx.contest)
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/list.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "problem_rows": problem_rows,
                "is_edit_allowed": is_edit,
                "is_remove_allowed": is_remove,
                "remove_blocked_reason": reason,
            },
        )
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.get("/new/{validator_type}", response_class=HTMLResponse, response_model=None, name="new_problem_form")
async def new_problem_form(
    request: Request,
    flash: FlashDep,
    validator_type: str,
    tab: str = Query(""),
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse | RedirectResponse:
    """Render the creation editor for one validation strategy.

    ``validator_type`` is taken as ``str`` and resolved in the handler on purpose:
    annotated as the enum, FastAPI answers ``422`` before the handler runs, which
    ``shared.error_handlers`` renders as a neutral JSON body -- wrong for an HTML
    admin page, and it would make an unknown strategy indistinguishable from the
    reserved one.
    """
    strategy = resolve_choice_or_redirect(request, flash, ctx.contest.login_slug, validator_type)
    if isinstance(strategy, RedirectResponse):
        return strategy
    templates = request.app.state.templates
    languages = await get_contest_languages(ctx.session, ctx.contest)
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/edit.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "problem": None,
                "validator_type": strategy,
                "is_interactive": strategy is ProblemValidatorType.INTERACTIVE,
                "view": build_problem_form_view(
                    request,
                    slug=ctx.contest.login_slug,
                    problem_id=None,
                    validator_type=strategy,
                    active_tab=tab or None,
                    save_disabled=not _is_edit_allowed(ctx.contest) and not _is_limits_edit_allowed(ctx.contest),
                    notices=editor_notices(ctx.contest, has_problem=False),
                ),
                "languages": languages,
                "limits_map": {},
                "has_pdf": False,
                "has_md": False,
                "md_content": "",
                "is_edit_allowed": _is_edit_allowed(ctx.contest),
                "is_limits_edit_allowed": _is_limits_edit_allowed(ctx.contest),
                "is_remove_allowed": False,
                "remove_blocked_reason": None,
                "category_names_csv": "",
                "errors": [],
                "field_errors": {},
                "first_error_field": "",
                "success": False,
                "balloon_colors": BALLOON_COLORS,
                "form_data": {
                    "color": "#000000",
                    "time_limit_ms": 1000,
                    "memory_limit_kb": 262144,
                    "pids_limit": 64,
                    "output_limit_in_bytes": "",
                    "image_caption": "",
                },
                "latest_profiling_run": None,
            },
        )
    )


@router.post("/new/{validator_type}", response_class=HTMLResponse, response_model=None, name="new_problem_submit")
async def new_problem_submit(
    request: Request,
    flash: FlashDep,
    validator_type: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
    title: str = Form(""),
    color: str = Form("#000000"),
    author: str = Form(""),
    notes: str = Form(""),
    time_limit_ms: str = Form(""),
    memory_limit_kb: str = Form(""),
    pids_limit: str = Form(""),
    output_limit_in_bytes: str = Form(""),
    category_names: str = Form(""),
    statement_file: UploadFile = File(None),
    statement_source: str = Form(""),
    md_content: str = Form(""),
    editorial: str = Form(""),
    image: UploadFile = File(None),
    image_caption: str = Form(""),
    active_tab: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    """Create one problem under the strategy named by the route parameter."""
    resolved = resolve_choice_or_redirect(request, flash, ctx.contest.login_slug, validator_type)
    if isinstance(resolved, RedirectResponse):
        return resolved
    # The strategy comes from the validated route parameter and from nowhere else:
    # any `validator_type` a crafted POST body carries is never read, so form
    # tampering cannot select or change one.
    strategy = resolved
    interactive = strategy is ProblemValidatorType.INTERACTIVE

    templates = request.app.state.templates
    form = await request.form()
    errors: list[str] = []
    field_errors: dict[str, str] = {}

    if not _is_edit_allowed(ctx.contest):
        errors.append("Contest is not editable.")

    title = title.strip()
    if not title:
        _contest_admin_problem_edit._add_field_error(errors, field_errors, "title", "Title is required.")
    elif len(title) > MAX_TITLE_CHARS:
        _contest_admin_problem_edit._add_field_error(
            errors,
            field_errors,
            "title",
            f"Title must be {MAX_TITLE_CHARS} characters or fewer.",
        )

    tlms = _contest_admin_problem_edit._positive_int(
        time_limit_ms, "Time limit (ms)", "time_limit_ms", errors, field_errors
    )
    mlkb = _contest_admin_problem_edit._positive_int(
        memory_limit_kb, "Memory limit (KB)", "memory_limit_kb", errors, field_errors
    )
    pl = _contest_admin_problem_edit._positive_int(pids_limit, "PIDs limit", "pids_limit", errors, field_errors)

    # A blank field resolves to the documented default: the column is NOT NULL,
    # so "no limit" is no longer expressible at the problem level.
    output_lim = DEFAULT_OUTPUT_LIMIT_BYTES
    if output_limit_in_bytes.strip():
        parsed_output_limit = _contest_admin_problem_edit._positive_int(
            output_limit_in_bytes,
            "Output limit",
            "output_limit_in_bytes",
            errors,
            field_errors,
        )
        if parsed_output_limit is not None:
            output_lim = parsed_output_limit

    pdf_bytes: bytes | None = None
    md_text: str | None = None
    if statement_source == "pdf":
        if not statement_file or not statement_file.filename:
            errors.append("A PDF statement file is required.")
        elif not statement_file.filename.lower().endswith(".pdf"):
            errors.append("Statement file must have .pdf extension.")
        else:
            pdf_bytes = await statement_file.read()
    elif statement_source == "md":
        if not md_content.strip():
            _contest_admin_problem_edit._add_field_error(
                errors,
                field_errors,
                "md_content",
                "Markdown statement cannot be empty.",
            )
        else:
            md_errors = validate_md_content(md_content)
            errors.extend(md_errors)
            if md_errors:
                field_errors["md_content"] = md_errors[0]
            if not md_errors:
                md_text = md_content
    else:
        errors.append("Problem statement is required.")

    if editorial.strip():
        editorial_errors = validate_md_content(editorial)
        if editorial_errors:
            _contest_admin_problem_edit._add_field_error(
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

    # Creation collects the problem *definition* only. Test cases, the custom
    # validator and sample interactions are authored on the judgment-data pages,
    # which this route redirects to on success -- so a form field naming any of
    # them is never read here.

    languages = await get_contest_languages(ctx.session, ctx.contest)
    language_limit_errors = _validate_language_limit_inputs(languages, form)
    errors.extend(language_limit_errors)

    if errors:
        form_data = {
            "title": title,
            "color": color,
            "author": author,
            "notes": notes,
            "time_limit_ms": time_limit_ms,
            "memory_limit_kb": memory_limit_kb,
            "pids_limit": pids_limit,
            "output_limit_in_bytes": output_limit_in_bytes,
            "image_caption": image_caption,
        }
        return _html(
            templates.TemplateResponse(
                request,
                "admin/problems/edit.html",
                {
                    "current_user": ctx.actor,
                    "contest": ctx.contest,
                    "problem": None,
                    "validator_type": strategy,
                    "is_interactive": interactive,
                    "view": build_problem_form_view(
                        request,
                        slug=ctx.contest.login_slug,
                        problem_id=None,
                        validator_type=strategy,
                        active_tab=_contest_admin_problem_edit._validation_tab(
                            field_errors,
                            language_limit_errors,
                            errors,
                            active_tab,
                        ),
                        save_disabled=(not _is_edit_allowed(ctx.contest) and not _is_limits_edit_allowed(ctx.contest)),
                        notices=editor_notices(ctx.contest, has_problem=False),
                    ),
                    "languages": languages,
                    "limits_map": {},
                    "has_pdf": False,
                    "has_md": statement_source == "md",
                    "md_content": md_content,
                    "editorial_content": editorial,
                    "is_edit_allowed": _is_edit_allowed(ctx.contest),
                    "is_limits_edit_allowed": _is_limits_edit_allowed(ctx.contest),
                    "is_remove_allowed": False,
                    "remove_blocked_reason": None,
                    "category_names_csv": category_names,
                    "errors": errors,
                    "field_errors": field_errors,
                    "first_error_field": next(iter(field_errors), ""),
                    "success": False,
                    "balloon_colors": BALLOON_COLORS,
                    "form_data": form_data,
                    "latest_profiling_run": None,
                },
                status_code=422,
            )
        )

    assert tlms is not None
    assert mlkb is not None
    assert pl is not None
    problem = Problem(
        title=title,
        validator_type=strategy,
        color=color,
        author=author.strip() or None,
        notes=notes.strip() or None,
        editorial=editorial if editorial.strip() else None,
        time_limit_ms=tlms,
        memory_limit_kb=mlkb,
        pids_limit=pl,
        output_limit_in_bytes=output_lim,
        problem_image_base64=image_b64,
        problem_image_mime=image_mime if image_b64 else None,
        problem_image_caption=image_caption.strip() or None,
    )
    await append_problem(ctx.session, ctx.contest, problem)

    await ctx.session.flush()

    cat_names = [n.strip() for n in category_names.split(",") if n.strip()]
    if cat_names:
        cats = await get_or_create_categories(ctx.session, cat_names)
        await replace_problem_categories(ctx.session, problem, cats)

    lang_limits = submitted_language_limits(languages, form, {})
    if lang_limits:
        await upsert_language_limits(ctx.session, problem, lang_limits)

    # Files are staged and swapped in as part of the commit, exactly as an edit
    # does. A create has nothing to displace, but it can still fail after writing,
    # and recovery reads the same journal: a problem row that never committed makes
    # its staged artifacts orphans, which reconciliation removes.
    statement_dir = settings.PROBLEM_STATEMENT_DIR
    swap = await open_save_swap(
        ctx.session,
        domain="contest",
        problem_id=problem.id,
        testcase_dir=settings.PROBLEM_TESTCASE_DIR,
    )
    try:
        # A new problem starts with no test cases; staging an empty directory is
        # what gives the problem a test-case directory to fill in on its judgment
        # pages.
        await stage_test_cases(swap, [], interactive=interactive)
        if pdf_bytes is not None:
            swap.stage_file(pdf_bytes, get_statement_path(problem.id, statement_dir), statement_dir)
        elif md_text is not None:
            swap.stage_file(md_text.encode("utf-8"), get_md_statement_path(problem.id, statement_dir), statement_dir)
    except Exception as exc:
        await abandon_swap(ctx.session, swap)
        flash(f"Could not create the problem: {exc}", FlashCategory.DANGER)
        return _redirect(
            str(request.url_for("new_problem_form", slug=ctx.contest.login_slug, validator_type=validator_type))
        )

    pid = problem.id
    await commit_with_edit_swap(ctx.session, swap)
    confirm_form_draft(
        request,
        problem_definition_draft_key("web", contest_id=ctx.contest.login_slug, validator_type=strategy.value),
    )
    flash("Problem created. Now add the data it will be judged against.", FlashCategory.SUCCESS)
    # An interactive problem cannot be judged at all until a validator compiles,
    # so creation lands on that page; everything else starts at its test cases.
    page = "validator" if interactive else "test-cases"
    return _redirect(judgment_page_url(request, ctx.contest.login_slug, pid, page))


# ---------------------------------------------------------------------------
# Move (HTMX)
# ---------------------------------------------------------------------------


@router.post("/{problem_id}/move", response_class=HTMLResponse, name="move_problem_htmx")
async def move_problem_htmx(
    request: Request,
    problem_id: str,
    direction: str | None = Query(None),
    new_ordinal: int | None = Query(None, le=PG_INT32_MAX),
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    if not _is_edit_allowed(ctx.contest):
        raise Exception("Contest is not editable")

    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")

    if new_ordinal is None:
        if direction == "up":
            new_ordinal = problem.ordinal - 1
        elif direction == "down":
            new_ordinal = problem.ordinal + 1
        else:
            raise HTTPException(status_code=400, detail="Provide new_ordinal or direction=up|down.")

    await move_problem(ctx.session, ctx.contest, problem, new_ordinal)
    await ctx.session.commit()

    problems = await get_contest_problems(ctx.session, ctx.contest)
    problem_rows = [(p, _label(p.ordinal), len(p.test_cases)) for p in problems]
    is_remove = _is_remove_allowed(ctx.contest)
    reason = _remove_blocked_reason(ctx.contest)

    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/list_table.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "problem_rows": problem_rows,
                "is_edit_allowed": _is_edit_allowed(ctx.contest),
                "is_remove_allowed": is_remove,
                "remove_blocked_reason": reason,
            },
        )
    )


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


@router.post("/{problem_id}/remove", name="remove_problem")
async def remove_problem(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> RedirectResponse:
    list_url = str(request.url_for("manage_problems", slug=ctx.contest.login_slug))

    if not _is_remove_allowed(ctx.contest):
        reason = _remove_blocked_reason(ctx.contest) or "Removal not allowed"
        flash(reason, FlashCategory.DANGER)
        return _redirect(list_url)

    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        flash("Problem not found.", FlashCategory.DANGER)
        return _redirect(list_url)

    pid = problem.id
    await remove_problem_and_resequence(ctx.session, ctx.contest, problem)
    await record_admin_action(
        ctx.session,
        request,
        module="web",
        actor_user_id=ctx.actor.id,
        actor_label=ctx.actor.username,
        action="delete",
        target_type="contest_problem",
        target_id=pid,
        detail=f"contest={ctx.contest.login_slug}",
    )
    await ctx.session.commit()

    try:
        await anyio.to_thread.run_sync(lambda: delete_problem_statement(pid, settings.PROBLEM_STATEMENT_DIR))
        await anyio.to_thread.run_sync(lambda: delete_all_testcase_files(pid, settings.PROBLEM_TESTCASE_DIR))
        # Removal is only allowed while the contest is upcoming, but admins and
        # judges are not blocked before the start, so a preview export may have
        # been cached; drop it here or it outlives the row it describes.
        if settings.PUBLIC_PROBLEM_PACK_PATH is not None:
            await discard_cached_export(export_cache_dir(settings.PUBLIC_PROBLEM_PACK_PATH), pid)
    except Exception:
        logger.exception("Failed to delete files for problem %s", pid)

    flash("Problem removed successfully.", FlashCategory.SUCCESS)
    return _redirect(list_url)
