#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_flash import FlashCategory, FlashDep

from shared.tc_zip import normalize_testcase_bytes
from web.config import settings
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.models.problem import ProblemTestCase
from web.routes.contest_admin_problem_helpers import (
    _delete_md_statement_for,
    _delete_pdf_statement_for,
    _delete_testcase_files_for,
    _html,
    _is_edit_allowed,
    _is_limits_edit_allowed,
    _is_remove_allowed,
    _read_testcase_preview_for,
    _redirect,
    _remove_blocked_reason,
    _renumber_testcase_files_for,
    _run_sync0,
    _save_md_statement_for,
    _save_problem_statement_for,
    _save_testcase_files_for,
    build_testcase_row_views,
)
from web.routes.contest_admin_problem_limits_helpers import (
    _build_profiling_limits_context,
    _validate_language_limit_inputs,
)
from web.services.category_service import get_or_create_categories, replace_problem_categories
from web.services.problem_service import (
    BALLOON_COLORS,
    append_test_case,
    changed_effective_limits,
    create_problem_limit_change_batch,
    get_contest_languages,
    get_language_limits_map,
    get_md_statement_path,
    get_problem_in_contest,
    get_statement_path,
    problem_fallback_limits,
    remove_test_case_and_resequence,
    submitted_language_limits,
    upsert_language_limits,
    validate_md_content,
)

router = APIRouter(prefix="/c/{slug}/admin/problems", tags=["contest_admin_problems"])


@router.get("/{problem_id}/edit", response_class=HTMLResponse, name="edit_problem_form")
async def edit_problem_form(
    request: Request,
    problem_id: str,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    templates = request.app.state.templates
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")

    statement_dir = settings.PROBLEM_STATEMENT_DIR
    testcase_dir = settings.PROBLEM_TESTCASE_DIR
    has_pdf = await anyio.to_thread.run_sync(lambda: get_statement_path(problem.id, statement_dir).exists())
    has_md = await anyio.to_thread.run_sync(lambda: get_md_statement_path(problem.id, statement_dir).exists())
    md_content_on_disk = ""
    if has_md:
        md_content_on_disk = await anyio.to_thread.run_sync(
            lambda: get_md_statement_path(problem.id, statement_dir).read_text(encoding="utf-8")
        )

    testcase_previews: dict[str, tuple[str, str]] = {}
    for tc in problem.test_cases:
        preview = await anyio.to_thread.run_sync(_read_testcase_preview_for(problem.id, tc.ordinal, testcase_dir))
        testcase_previews[tc.id] = preview

    form_data = {
        "title": problem.title,
        "color": problem.color or "#000000",
        "author": problem.author or "",
        "notes": problem.notes or "",
        "time_limit_ms": problem.time_limit_ms,
        "memory_limit_kb": problem.memory_limit_kb,
        "pids_limit": problem.pids_limit,
        "output_limit_in_bytes": problem.output_limit_in_bytes or "",
    }
    active_tab = request.query_params.get("tab", "content")
    if active_tab not in {"content", "limits"}:
        active_tab = "content"
    profiling_limits_context = await _build_profiling_limits_context(request, ctx, problem, form_data)

    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/edit.html",
            {
                "current_user": ctx.actor,
                "problem": problem,
                "testcase_previews": testcase_previews,
                "rows": build_testcase_row_views(request, ctx.contest, list(problem.test_cases), testcase_previews),
                "has_pdf": has_pdf,
                "has_md": has_md,
                "md_content": md_content_on_disk,
                "is_remove_allowed": _is_remove_allowed(ctx.contest),
                "remove_blocked_reason": _remove_blocked_reason(ctx.contest),
                "category_names_csv": ",".join(cat.name for cat in problem.categories),
                "errors": [],
                "active_tab": active_tab,
                "balloon_colors": BALLOON_COLORS,
                **profiling_limits_context,
            },
        )
    )


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
    active_tab: str = Form("content"),
    category_names: str = Form(""),
    statement_file: UploadFile = File(None),
    statement_source: str = Form("unchanged"),
    md_content: str = Form(""),
) -> HTMLResponse | RedirectResponse:
    problem = await get_problem_in_contest(ctx.session, ctx.contest, problem_id)
    if problem is None:
        raise Exception("Problem not found")

    templates = request.app.state.templates
    form = await request.form()
    languages = await get_contest_languages(ctx.session, ctx.contest)
    existing_limits = await get_language_limits_map(ctx.session, problem)
    language_limit_errors = _validate_language_limit_inputs(languages, form)

    if not _is_edit_allowed(ctx.contest) and _is_limits_edit_allowed(ctx.contest) and active_tab == "limits":
        if language_limit_errors:
            statement_dir = settings.PROBLEM_STATEMENT_DIR
            existing_has_pdf = await anyio.to_thread.run_sync(
                lambda: get_statement_path(problem.id, statement_dir).exists()
            )
            existing_has_md = await anyio.to_thread.run_sync(
                lambda: get_md_statement_path(problem.id, statement_dir).exists()
            )
            testcase_dir = settings.PROBLEM_TESTCASE_DIR
            preview_map: dict[str, tuple[str, str]] = {}
            for tc in problem.test_cases:
                preview = await anyio.to_thread.run_sync(
                    _read_testcase_preview_for(problem.id, tc.ordinal, testcase_dir)
                )
                preview_map[tc.id] = preview
            form_data = {
                "title": problem.title,
                "color": problem.color or "#000000",
                "author": problem.author or "",
                "notes": problem.notes or "",
                "time_limit_ms": problem.time_limit_ms,
                "memory_limit_kb": problem.memory_limit_kb,
                "pids_limit": problem.pids_limit,
                "output_limit_in_bytes": problem.output_limit_in_bytes or "",
            }
            profiling_limits_context = await _build_profiling_limits_context(request, ctx, problem, form_data)
            return _html(
                templates.TemplateResponse(
                    request,
                    "admin/problems/edit.html",
                    {
                        "current_user": ctx.actor,
                        "problem": problem,
                        "testcase_previews": preview_map,
                        "rows": build_testcase_row_views(request, ctx.contest, list(problem.test_cases), preview_map),
                        "has_pdf": existing_has_pdf,
                        "has_md": existing_has_md,
                        "md_content": "",
                        "is_remove_allowed": _is_remove_allowed(ctx.contest),
                        "remove_blocked_reason": _remove_blocked_reason(ctx.contest),
                        "edit_tc_id": None,
                        "edit_tc_content": None,
                        "category_names_csv": ", ".join(category.name for category in problem.categories),
                        "errors": language_limit_errors,
                        "success": False,
                        "active_tab": "limits",
                        "balloon_colors": BALLOON_COLORS,
                        **profiling_limits_context,
                    },
                    status_code=422,
                )
            )
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
        batch = await create_problem_limit_change_batch(
            ctx.session,
            ctx.contest,
            problem,
            ctx.actor,
            changed_limits,
        )
        await ctx.session.commit()
        if batch is not None:
            flash("Limits saved successfully. Review the affected submissions batch.", FlashCategory.SUCCESS)
            return _redirect(
                str(
                    request.url_for(
                        "problem_limit_change_batch_review",
                        slug=ctx.contest.login_slug,
                        problem_id=problem.id,
                        batch_id=batch.id,
                    )
                )
            )
        flash("Limits saved successfully. No affected submissions were found.", FlashCategory.SUCCESS)
        return _redirect(
            str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem.id))
            + "?tab=limits"
        )

    errors: list[str] = []
    if not _is_edit_allowed(ctx.contest):
        errors.append("Contest is not editable.")
    errors.extend(language_limit_errors)

    title = title.strip()
    if not title:
        errors.append("Title is required.")
    elif len(title) > 200:
        errors.append("Title must be 200 characters or fewer.")

    tlms: int | None = None
    if not time_limit_ms.strip():
        errors.append("Time limit (ms) is required.")
    else:
        try:
            tlms = int(time_limit_ms)
            if tlms < 1:
                errors.append("Time limit must be >= 1.")
        except ValueError:
            errors.append("Time limit must be a positive integer.")

    mlkb: int | None = None
    if not memory_limit_kb.strip():
        errors.append("Memory limit (KB) is required.")
    else:
        try:
            mlkb = int(memory_limit_kb)
            if mlkb < 1:
                errors.append("Memory limit must be >= 1.")
        except ValueError:
            errors.append("Memory limit must be a positive integer.")

    pids_limit_value: int | None = None
    if not pids_limit.strip():
        errors.append("PIDs limit is required.")
    else:
        try:
            pids_limit_value = int(pids_limit)
            if pids_limit_value < 1:
                errors.append("PIDs limit must be >= 1.")
        except ValueError:
            errors.append("PIDs limit must be a positive integer.")

    output_limit_value: int | None = None
    if output_limit_in_bytes.strip():
        try:
            output_limit_value = int(output_limit_in_bytes)
        except ValueError:
            errors.append("Output limit must be a positive integer or blank.")

    statement_dir = settings.PROBLEM_STATEMENT_DIR
    existing_has_pdf = await anyio.to_thread.run_sync(lambda: get_statement_path(problem.id, statement_dir).exists())
    existing_has_md = await anyio.to_thread.run_sync(lambda: get_md_statement_path(problem.id, statement_dir).exists())
    result_is_md = existing_has_md

    if statement_source == "pdf":
        result_is_md = False
        if not statement_file or not statement_file.filename:
            errors.append("A PDF statement file is required.")
        elif not statement_file.filename.lower().endswith(".pdf"):
            errors.append("Statement file must have .pdf extension.")
    elif statement_source == "md":
        result_is_md = True
        if not md_content.strip():
            errors.append("Markdown statement cannot be empty.")
        else:
            errors.extend(validate_md_content(md_content))
    elif statement_source == "unchanged":
        result_is_md = existing_has_md
    else:
        errors.append("Invalid statement source.")

    tc_ids_to_remove = {value.strip() for value in str(form.get("tc_remove_ids", "") or "").split(",") if value.strip()}
    tcs_to_remove = [tc for tc in problem.test_cases if tc.id in tc_ids_to_remove]
    if len(problem.test_cases) - len(tcs_to_remove) == 0:
        errors.append("At least one test case is required.")

    if errors:
        testcase_dir = settings.PROBLEM_TESTCASE_DIR
        preview_map_for_errors: dict[str, tuple[str, str]] = {}
        for tc in problem.test_cases:
            preview = await anyio.to_thread.run_sync(_read_testcase_preview_for(problem.id, tc.ordinal, testcase_dir))
            preview_map_for_errors[tc.id] = preview
        form_data = {
            "title": title,
            "color": color,
            "author": author,
            "notes": notes,
            "time_limit_ms": time_limit_ms,
            "memory_limit_kb": memory_limit_kb,
            "pids_limit": pids_limit,
            "output_limit_in_bytes": output_limit_in_bytes,
        }
        profiling_limits_context = await _build_profiling_limits_context(request, ctx, problem, form_data)
        selected_tab = active_tab if active_tab in {"content", "limits"} else "content"
        return _html(
            templates.TemplateResponse(
                request,
                "admin/problems/edit.html",
                {
                    "current_user": ctx.actor,
                    "problem": problem,
                    "testcase_previews": preview_map_for_errors,
                    "rows": build_testcase_row_views(
                        request, ctx.contest, list(problem.test_cases), preview_map_for_errors
                    ),
                    "has_pdf": statement_source == "unchanged" and existing_has_pdf,
                    "has_md": (statement_source == "unchanged" and existing_has_md) or statement_source == "md",
                    "md_content": md_content,
                    "is_remove_allowed": _is_remove_allowed(ctx.contest),
                    "remove_blocked_reason": _remove_blocked_reason(ctx.contest),
                    "edit_tc_id": None,
                    "edit_tc_content": None,
                    "category_names_csv": category_names,
                    "errors": errors,
                    "success": False,
                    "active_tab": selected_tab,
                    "balloon_colors": BALLOON_COLORS,
                    **profiling_limits_context,
                },
                status_code=422,
            )
        )

    assert tlms is not None
    assert mlkb is not None
    assert pids_limit_value is not None
    problem.title = title
    problem.color = color
    problem.author = author.strip() or None
    problem.notes = notes.strip() or None
    problem.time_limit_ms = tlms
    problem.memory_limit_kb = mlkb
    problem.pids_limit = pids_limit_value
    problem.output_limit_in_bytes = output_limit_value

    if statement_source == "pdf":
        assert statement_file is not None
        pdf_bytes = await statement_file.read()
        await anyio.to_thread.run_sync(_run_sync0(_save_problem_statement_for(problem.id, pdf_bytes, statement_dir)))
        await anyio.to_thread.run_sync(_run_sync0(_delete_md_statement_for(problem.id, statement_dir)))
    elif statement_source == "md":
        await anyio.to_thread.run_sync(_run_sync0(_save_md_statement_for(problem.id, md_content, statement_dir)))
        await anyio.to_thread.run_sync(_run_sync0(_delete_pdf_statement_for(problem.id, statement_dir)))

    category_names_list = [name.strip() for name in category_names.split(",") if name.strip()]
    categories = await get_or_create_categories(ctx.session, category_names_list)
    await replace_problem_categories(ctx.session, problem, categories)

    lang_limits = submitted_language_limits(languages, form, existing_limits)
    await upsert_language_limits(ctx.session, problem, lang_limits)

    testcase_dir = settings.PROBLEM_TESTCASE_DIR
    for tc in tcs_to_remove:
        removed_ordinal = tc.ordinal
        total_before = len(problem.test_cases)
        await anyio.to_thread.run_sync(
            _run_sync0(_delete_testcase_files_for(problem.id, removed_ordinal, testcase_dir))
        )
        await remove_test_case_and_resequence(ctx.session, problem, tc)
        for old_ordinal in range(removed_ordinal + 1, total_before + 1):
            await anyio.to_thread.run_sync(
                _run_sync0(_renumber_testcase_files_for(problem.id, old_ordinal, old_ordinal - 1, testcase_dir))
            )

    # Append inline add-rows (parity with Arena inline test-case creation). Rows
    # are appended after removals so ordinals stay contiguous; files are written
    # only once the DB transaction is durable.
    pending_writes: list[tuple[int, bytes, bytes]] = []
    add_indices = sorted(
        {
            int(key.rsplit("_", 1)[1])
            for key in form
            if (key.startswith("tc_in_") or key.startswith("tc_out_")) and key.rsplit("_", 1)[1].isdigit()
        }
    )
    for i in add_indices:
        in_val = str(form.get(f"tc_in_{i}", ""))
        out_val = str(form.get(f"tc_out_{i}", ""))
        if not in_val and not out_val:
            continue
        explanation = str(form.get(f"tc_explanation_{i}", "")).strip() or None
        in_bytes = in_val.encode()
        out_bytes = out_val.encode()
        new_tc = ProblemTestCase(is_sample=bool(form.get(f"tc_is_sample_{i}")), explanation=explanation)
        await append_test_case(ctx.session, problem, new_tc)
        new_tc.input_size_bytes = len(normalize_testcase_bytes(in_bytes))
        new_tc.output_size_bytes = len(normalize_testcase_bytes(out_bytes))
        pending_writes.append((new_tc.ordinal, in_bytes, out_bytes))

    await ctx.session.commit()
    for ordinal, in_bytes, out_bytes in pending_writes:
        await anyio.to_thread.run_sync(
            _run_sync0(_save_testcase_files_for(problem.id, ordinal, in_bytes, out_bytes, testcase_dir))
        )
    flash("Changes saved successfully.", FlashCategory.SUCCESS)
    if result_is_md:
        return _redirect(str(request.url_for("edit_problem_form", slug=ctx.contest.login_slug, problem_id=problem_id)))
    return _redirect(str(request.url_for("manage_problems", slug=ctx.contest.login_slug)))
