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

from shared.http_params import PG_INT32_MAX
from shared.services.admin_audit import record_admin_action
from shared.services.custom_validator import (
    build_validation_job,
    parse_validator_source,
    stage_candidate,
)
from shared.services.imageprocessing_service import ImageProcessingError
from shared.services.problem_image import process_problem_image_upload
from shared.services.problem_package import DEFAULT_OUTPUT_LIMIT_BYTES, MAX_TITLE_CHARS
from shared.services.valkey_service import enqueue_custom_validator_validation_job
from shared.tc_zip import parse_single_testcase_zip
from web.config import settings
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.models.problem import Problem, ProblemCustomValidator, ProblemTestCase
from web.routes import contest_admin_problem_edit as _contest_admin_problem_edit
from web.routes import contest_admin_problem_limits as _contest_admin_problem_limits
from web.routes.contest_admin_problem_helpers import (
    _html,
    _is_edit_allowed,
    _is_limits_edit_allowed,
    _is_remove_allowed,
    _label,
    _redirect,
    _remove_blocked_reason,
    _run_sync0,
    _save_md_statement_for,
    _save_problem_statement_for,
    _save_testcase_files_for,
)
from web.routes.contest_admin_problem_limits_helpers import _validate_language_limit_inputs
from web.services.category_service import get_or_create_categories, replace_problem_categories
from web.services.problem_service import (
    BALLOON_COLORS,
    append_problem,
    append_test_case,
    delete_all_testcase_files,
    delete_problem_statement,
    get_active_languages,
    get_contest_languages,
    get_contest_problems,
    get_problem_in_contest,
    move_problem,
    parse_testcases_zip,
    remove_problem_and_resequence,
    submitted_language_limits,
    upsert_language_limits,
    validate_md_content,
)

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


@router.get("/new", response_class=HTMLResponse, name="new_problem_form")
async def new_problem_form(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    languages = await get_contest_languages(ctx.session, ctx.contest)
    validator_languages = await get_active_languages(ctx.session)
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/edit.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "problem": None,
                "languages": languages,
                "limits_map": {},
                "testcase_previews": {},
                "has_pdf": False,
                "has_md": False,
                "md_content": "",
                "is_edit_allowed": _is_edit_allowed(ctx.contest),
                "is_limits_edit_allowed": _is_limits_edit_allowed(ctx.contest),
                "is_remove_allowed": False,
                "remove_blocked_reason": None,
                "edit_tc_id": None,
                "edit_tc_content": None,
                "category_names_csv": "",
                "errors": [],
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
                "validator_languages": validator_languages,
                "validator_status": None,
            },
        )
    )


@router.post("/new", response_class=HTMLResponse, response_model=None, name="new_problem_submit")
async def new_problem_submit(
    request: Request,
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
    category_names: str = Form(""),
    statement_file: UploadFile = File(None),
    statement_source: str = Form(""),
    md_content: str = Form(""),
    testcases_zip: UploadFile = File(None),
    validator_language_id: str = Form(""),
    validator_source_file: UploadFile = File(None),
    image: UploadFile = File(None),
    image_caption: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    templates = request.app.state.templates
    form = await request.form()
    errors: list[str] = []

    if not _is_edit_allowed(ctx.contest):
        errors.append("Contest is not editable.")

    title = title.strip()
    if not title:
        errors.append("Title is required.")
    elif len(title) > MAX_TITLE_CHARS:
        errors.append(f"Title must be {MAX_TITLE_CHARS} characters or fewer.")

    tlms = None
    if not time_limit_ms.strip():
        errors.append("Time limit (ms) is required.")
    else:
        try:
            tlms = int(time_limit_ms)
            if tlms < 1:
                errors.append("Time limit must be >= 1.")
        except ValueError:
            errors.append("Time limit must be a positive integer.")

    mlkb = None
    if not memory_limit_kb.strip():
        errors.append("Memory limit (KB) is required.")
    else:
        try:
            mlkb = int(memory_limit_kb)
            if mlkb < 1:
                errors.append("Memory limit must be >= 1.")
        except ValueError:
            errors.append("Memory limit must be a positive integer.")

    pl = None
    if not pids_limit.strip():
        errors.append("PIDs limit is required.")
    else:
        try:
            pl = int(pids_limit)
            if pl < 1:
                errors.append("PIDs limit must be >= 1.")
        except ValueError:
            errors.append("PIDs limit must be a positive integer.")

    # A blank field resolves to the documented default: the column is NOT NULL,
    # so "no limit" is no longer expressible at the problem level.
    output_lim = DEFAULT_OUTPUT_LIMIT_BYTES
    if output_limit_in_bytes.strip():
        try:
            output_lim = int(output_limit_in_bytes)
            if output_lim < 1:
                errors.append("Output limit must be at least 1 byte.")
        except ValueError:
            errors.append("Output limit must be a positive integer.")

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
            errors.append("Markdown statement cannot be empty.")
        else:
            md_errors = validate_md_content(md_content)
            errors.extend(md_errors)
            if not md_errors:
                md_text = md_content
    else:
        errors.append("Problem statement is required.")

    image_b64: str | None = None
    image_mime: str | None = None
    if image and image.filename:
        try:
            image_b64, image_mime = await process_problem_image_upload(request.app.state.image_service, image)
        except (ImageProcessingError, ValueError) as exc:
            errors.append(f"Problem image: {exc}")

    tc_list: list[tuple[bytes, bytes | None, bool, str | None]] = []

    validator_source: str | None = None
    validator_language_id = validator_language_id.strip()
    validator_file_supplied = bool(validator_source_file and validator_source_file.filename)
    if validator_file_supplied != bool(validator_language_id):
        errors.append("Choose a validator language and source file together.")
    elif validator_file_supplied:
        try:
            validator_source = parse_validator_source(await validator_source_file.read())
        except ValueError as exc:
            errors.append(str(exc))

    # An interactive problem's cases carry input only: the input parametrizes the
    # validator, which decides the verdict instead of an expected-output file.
    interactive = validator_source is not None

    if testcases_zip and testcases_zip.filename:
        zip_bytes_data = await testcases_zip.read()
        try:
            parsed = parse_testcases_zip(zip_bytes_data, require_output=not interactive)
            for source_ordinal, (in_b, out_b) in sorted(parsed.pairs.items()):
                tc_list.append((in_b, out_b, False, parsed.explanations.get(source_ordinal)))
        except ValueError as exc:
            errors.append(f"Test case ZIP error: {exc}")

    tc_indices = sorted(
        {
            int(key.rsplit("_", 1)[1])
            for key in form
            if (key.startswith("tc_in_") or key.startswith("tc_out_")) and key.rsplit("_", 1)[1].isdigit()
        }
    )
    for i in tc_indices:
        in_val = str(form.get(f"tc_in_{i}", ""))
        out_val = str(form.get(f"tc_out_{i}", ""))
        is_sample = bool(form.get(f"tc_is_sample_{i}"))
        raw_explanation = str(form.get(f"tc_explanation_{i}", "")).strip()
        out_bytes = None if interactive else out_val.encode()
        tc_list.append((in_val.encode(), out_bytes, is_sample, raw_explanation or None))

    zip_indices = sorted(
        {
            int(key.rsplit("_", 1)[1])
            for key in form
            if key.startswith("tc_zip_") and not key.startswith("tc_zip_is_sample_") and key.rsplit("_", 1)[1].isdigit()
        }
    )
    for i in zip_indices:
        upload = form.get(f"tc_zip_{i}")
        if not upload or not hasattr(upload, "read"):
            continue
        zip_data = await upload.read()
        if not zip_data:
            continue
        try:
            single = parse_single_testcase_zip(zip_data, require_output=not interactive)
        except ValueError as exc:
            errors.append(f"Test case ZIP #{i + 1} error: {exc}")
            continue
        is_sample = bool(form.get(f"tc_zip_is_sample_{i}"))
        tc_list.append((single.input_bytes, single.output_bytes, is_sample, single.explanation))

    if not tc_list:
        errors.append("At least one test case is required.")

    languages = await get_contest_languages(ctx.session, ctx.contest)
    validator_languages = await get_active_languages(ctx.session)
    if validator_language_id and validator_language_id not in {language.id for language in validator_languages}:
        errors.append("Validator language is not active.")
    errors.extend(_validate_language_limit_inputs(languages, form))

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
                    "languages": languages,
                    "limits_map": {},
                    "testcase_previews": {},
                    "has_pdf": False,
                    "has_md": statement_source == "md",
                    "md_content": md_content,
                    "is_edit_allowed": _is_edit_allowed(ctx.contest),
                    "is_limits_edit_allowed": _is_limits_edit_allowed(ctx.contest),
                    "is_remove_allowed": False,
                    "remove_blocked_reason": None,
                    "edit_tc_id": None,
                    "edit_tc_content": None,
                    "category_names_csv": category_names,
                    "errors": errors,
                    "success": False,
                    "balloon_colors": BALLOON_COLORS,
                    "form_data": form_data,
                    "latest_profiling_run": None,
                    "validator_languages": validator_languages,
                    "validator_status": None,
                    "validator_language_id": validator_language_id,
                },
                status_code=422,
            )
        )

    assert tlms is not None
    assert mlkb is not None
    assert pl is not None
    problem = Problem(
        title=title,
        color=color,
        author=author.strip() or None,
        notes=notes.strip() or None,
        time_limit_ms=tlms,
        memory_limit_kb=mlkb,
        pids_limit=pl,
        output_limit_in_bytes=output_lim,
        problem_image_base64=image_b64,
        problem_image_mime=image_mime if image_b64 else None,
        problem_image_caption=image_caption.strip() or None,
    )
    await append_problem(ctx.session, ctx.contest, problem)

    statement_dir = settings.PROBLEM_STATEMENT_DIR
    if pdf_bytes is not None:
        await anyio.to_thread.run_sync(_run_sync0(_save_problem_statement_for(problem.id, pdf_bytes, statement_dir)))
    elif md_text is not None:
        await anyio.to_thread.run_sync(_run_sync0(_save_md_statement_for(problem.id, md_text, statement_dir)))

    # The test cases land before the validator is staged, so the problem's cases are
    # already durable and already forced secret by the time anything can observe it
    # as interactive. Staging first would leave a window in which a reader sees a
    # validator-configured problem whose cases are still public or absent.
    testcase_dir = settings.PROBLEM_TESTCASE_DIR
    for in_b, out_b, is_sample, explanation in tc_list:
        # An interactive problem shows sample interactions, never sample cases, so
        # a sample flag checked on the create form is discarded here.
        tc = ProblemTestCase(is_sample=not interactive and is_sample, explanation=explanation)
        await append_test_case(ctx.session, problem, tc)
        ordinal = tc.ordinal
        in_size, out_size = await anyio.to_thread.run_sync(
            _run_sync0(_save_testcase_files_for(problem.id, ordinal, in_b, out_b, testcase_dir))
        )
        tc.input_size_bytes = in_size
        tc.output_size_bytes = out_size
    await ctx.session.flush()

    candidate_token: str | None = None
    if validator_source is not None:
        validator = ProblemCustomValidator(problem_id=problem.id)
        candidate_token = stage_candidate(
            validator,
            language_id=validator_language_id,
            source=validator_source,
        )
        ctx.session.add(validator)

    cat_names = [n.strip() for n in category_names.split(",") if n.strip()]
    if cat_names:
        cats = await get_or_create_categories(ctx.session, cat_names)
        await replace_problem_categories(ctx.session, problem, cats)

    lang_limits = submitted_language_limits(languages, form, {})
    if lang_limits:
        await upsert_language_limits(ctx.session, problem, lang_limits)

    await ctx.session.commit()
    pid = problem.id
    if candidate_token is not None:
        await enqueue_custom_validator_validation_job(
            request.app.state.valkey_runtime,
            build_validation_job(domain="contest", problem_id=pid, candidate_token=candidate_token),
        )
    flash("Problem created successfully.", FlashCategory.SUCCESS)
    if md_text is not None or candidate_token is not None:
        return _redirect(str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=pid)))
    return _redirect(str(request.url_for("manage_problems", slug=ctx.contest.login_slug)))


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
    except Exception:
        logger.exception("Failed to delete files for problem %s", pid)

    flash("Problem removed successfully.", FlashCategory.SUCCESS)
    return _redirect(list_url)
