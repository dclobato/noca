#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin routes for problem management (list, create, edit, toggle).

Presentation helpers (URL/context builders, form rendering) live in
``admin_problem_form_views.py``. Test-case sub-routes live in
``admin_problem_tc.py``; JSON API endpoints live in ``admin_problem_api.py``.
"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.database import get_db
from arena.dependencies.admin import require_arena_problem_editor
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_common import get_problem_or_403, validator_languages
from arena.routes.admin_problem_form_views import (
    edit_form_extras,
    effective_per_page,
    form_fields,
    html_response,
    is_admin,
    problem_list_url,
    process_problem_image,
    render_problem_form,
    return_state,
    safe_next_path,
    selected_cats_data,
)
from arena.services import admin_problem_service, admin_problem_tc_pending, admin_problem_tc_service
from arena.services.admin_problem_validator_service import (
    parse_validator_upload,
    stage_candidate_revision,
)
from arena.services.pagination_service import parse_page
from shared.services.admin_audit import record_admin_action
from shared.services.custom_validator import status_view
from shared.services.imageprocessing_service import ImageProcessingError
from shared.services.valkey_service import enqueue_custom_validator_validation_job
from shared.services.valkey_service.queue_ops import enqueue_arena_submission_job

router = APIRouter(prefix="/admin", tags=["arena-admin"])


@router.get("/problems", response_class=HTMLResponse, name="arena_admin_problem_list")
async def admin_problem_list(
    request: Request,
    flash: FlashDep,
    page: str | None = None,
    per_page: str | None = None,
    search: str = "",
    sort_by: str = "number_asc",
    owner_id: str = "",
    category_slugs: list[str] | None = Query(None),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the paginated Arena problem management list."""
    per_page_value = effective_per_page(per_page)
    is_adm = is_admin(current_user)

    pagination = await admin_problem_service.list_problems_paginated(
        session,
        page=parse_page(page),
        per_page=per_page_value,
        search=search,
        category_slugs=category_slugs or [],
        owner_id=owner_id if (is_adm and owner_id) else None,
        sort_by=sort_by,
        caller_id=current_user.id,
        is_admin=is_adm,
    )
    owners = await admin_problem_service.list_owners(session) if is_adm else []
    all_categories = await admin_problem_service.search_categories(session, query="", limit=200)
    templates = request.app.state.arena_templates
    return html_response(
        templates.TemplateResponse(
            request,
            "admin/problem_list.html",
            {
                "pagination": pagination,
                "per_page": per_page_value,
                "search": search,
                "sort_by": sort_by,
                "selected_owner_id": owner_id,
                "selected_category_slugs": set(category_slugs or []),
                "owners": owners,
                "all_categories": all_categories,
                "current_user": current_user,
                "is_admin": is_adm,
            },
        )
    )


@router.get("/problems/new", response_class=HTMLResponse, name="arena_admin_problem_new")
async def admin_problem_new(
    request: Request,
    flash: FlashDep,
    page: str = "1",
    per_page: str = "25",
    search: str = "",
    sort_by: str = "title_asc",
    owner_id: str = "",
    category_slugs: list[str] | None = Query(None),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the create-problem form."""
    back_url = problem_list_url(
        request,
        page=page,
        per_page=per_page,
        search=search,
        sort_by=sort_by,
        owner_id=owner_id,
        category_slugs=category_slugs,
    )
    return render_problem_form(
        request,
        mode="create",
        form=form_fields(
            title="",
            author="",
            author_is_owner=True,
            source="",
            hide_author_show_source=False,
            time_limit_ms=1000,
            memory_limit_kb=262144,
            pids_limit=64,
            output_limit_in_bytes=65536,
            problem_statement="",
            category_ids=[],
            image_caption="",
        ),
        cats_data=[],
        back_url=back_url,
        state=return_state(
            page=page,
            per_page=per_page,
            search=search,
            sort_by=sort_by,
            owner_id=owner_id,
            category_slugs=category_slugs,
        ),
        current_user=current_user,
        validator_languages=await validator_languages(session),
    )


@router.post("/problems/new", name="arena_admin_problem_create")
async def admin_problem_create(
    request: Request,
    flash: FlashDep,
    title: str = Form(""),
    author: str = Form(""),
    author_is_owner: bool = Form(False),
    source: str = Form(""),
    hide_author_show_source: bool = Form(False),
    time_limit_ms: int = Form(1000),
    memory_limit_kb: int = Form(262144),
    pids_limit: int = Form(64),
    output_limit_in_bytes: int = Form(65536),
    problem_statement: str = Form(""),
    category_ids: list[str] = Form(default=[]),
    return_page: str = Form("1"),
    return_per_page: str = Form("25"),
    return_search: str = Form(""),
    return_sort_by: str = Form("title_asc"),
    return_owner_id: str = Form(""),
    return_category_slugs: list[str] = Form(default=[]),
    image: UploadFile = File(None),
    image_caption: str = Form(""),
    notes: str = Form(""),
    license: str = Form(""),
    validator_language_id: str = Form(""),
    validator_source_file: UploadFile = File(None),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Create a new Arena problem (always starts as disabled)."""
    back_url = problem_list_url(
        request,
        page=return_page,
        per_page=return_per_page,
        search=return_search,
        sort_by=return_sort_by,
        owner_id=return_owner_id,
        category_slugs=return_category_slugs,
    )
    state = return_state(
        page=return_page,
        per_page=return_per_page,
        search=return_search,
        sort_by=return_sort_by,
        owner_id=return_owner_id,
        category_slugs=return_category_slugs,
    )
    form = form_fields(
        title=title,
        author=author,
        author_is_owner=author_is_owner,
        source=source,
        hide_author_show_source=hide_author_show_source,
        time_limit_ms=time_limit_ms,
        memory_limit_kb=memory_limit_kb,
        pids_limit=pids_limit,
        output_limit_in_bytes=output_limit_in_bytes,
        problem_statement=problem_statement,
        category_ids=category_ids,
        image_caption=image_caption,
        notes=notes,
        license=license,
    )

    async def render_error() -> HTMLResponse:
        all_categories = await admin_problem_service.search_categories(session, query="", limit=200)
        return render_problem_form(
            request,
            mode="create",
            form=form,
            cats_data=selected_cats_data(all_categories, category_ids),
            back_url=back_url,
            state=state,
            current_user=current_user,
            validator_languages=await validator_languages(session),
            status_code=400,
        )

    # Check the validator upload before creating anything, so a bad file cannot
    # burn an Arena problem number.
    try:
        validator_upload = await parse_validator_upload(
            session,
            language_id=validator_language_id,
            source_file=validator_source_file,
        )
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return await render_error()

    image_b64: str | None = None
    image_mime: str | None = None
    if image and image.filename:
        try:
            image_b64, image_mime = await process_problem_image(request, image)
        except (ImageProcessingError, ValueError) as exc:
            flash(str(exc), FlashCategory.DANGER)
            return await render_error()

    try:
        problem = await admin_problem_service.create_problem(
            session,
            caller_id=current_user.id,
            title=title,
            author=author or None,
            author_is_owner=author_is_owner,
            source=source or None,
            hide_author_show_source=hide_author_show_source,
            time_limit_ms=time_limit_ms,
            memory_limit_kb=memory_limit_kb,
            pids_limit=pids_limit,
            output_limit_in_bytes=output_limit_in_bytes,
            problem_statement=problem_statement,
            image_b64=image_b64,
            image_mime=image_mime,
            image_caption=image_caption or None,
            notes=notes or None,
            license=license or None,
            category_ids=category_ids,
        )
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return await render_error()

    validation_job = (
        stage_candidate_revision(session, problem, validator_upload) if validator_upload is not None else None
    )

    await session.commit()
    if validation_job is not None:
        await enqueue_custom_validator_validation_job(request.app.state.valkey_runtime, validation_job)
    flash(f"Problem #{problem.arena_number} created (disabled).", FlashCategory.SUCCESS)
    if validation_job is not None:
        return RedirectResponse(request.url_for("arena_admin_problem_edit", problem_id=problem.id), 303)
    return RedirectResponse(
        url=problem_list_url(
            request,
            page=return_page,
            per_page=return_per_page,
            search=return_search,
            sort_by=return_sort_by,
            owner_id=return_owner_id,
            category_slugs=return_category_slugs,
            anchor=problem.id,
        ),
        status_code=303,
    )


@router.get(
    "/problems/{problem_id}/edit",
    response_class=HTMLResponse,
    name="arena_admin_problem_edit",
)
async def admin_problem_edit(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    page: str = "1",
    per_page: str = "25",
    search: str = "",
    sort_by: str = "title_asc",
    owner_id: str = "",
    category_slugs: list[str] | None = Query(None),
    next: str = Query(""),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the edit form for an existing problem."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    test_cases, all_categories, problem_owner, has_submissions = await edit_form_extras(problem, current_user, session)
    selected_ids = [cat.id for cat in problem.categories]
    safe_next = safe_next_path(next)
    back_url = safe_next or problem_list_url(
        request,
        page=page,
        per_page=per_page,
        search=search,
        sort_by=sort_by,
        owner_id=owner_id,
        category_slugs=category_slugs,
    )
    return render_problem_form(
        request,
        mode="edit",
        problem=problem,
        test_cases=test_cases,
        form=form_fields(
            title=problem.title,
            author=problem.author or "",
            author_is_owner=problem.author_is_owner,
            source=problem.source or "",
            hide_author_show_source=problem.hide_author_show_source,
            time_limit_ms=problem.time_limit_ms,
            memory_limit_kb=problem.memory_limit_kb,
            pids_limit=problem.pids_limit,
            output_limit_in_bytes=problem.output_limit_in_bytes,
            problem_statement=problem.problem_statement,
            category_ids=selected_ids,
            image_caption=problem.problem_image_caption or "",
            notes=problem.notes or "",
            license=problem.license or "",
        ),
        cats_data=selected_cats_data(all_categories, selected_ids),
        back_url=back_url,
        next_url=safe_next,
        state=return_state(
            page=page,
            per_page=per_page,
            search=search,
            sort_by=sort_by,
            owner_id=owner_id,
            category_slugs=category_slugs,
        ),
        problem_owner=problem_owner,
        has_submissions=has_submissions,
        validator_status=status_view(problem.custom_validator),
        validator_languages=await validator_languages(session),
        current_user=current_user,
    )


@router.post("/problems/{problem_id}/edit", name="arena_admin_problem_update")
async def admin_problem_update(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    title: str = Form(""),
    author: str = Form(""),
    author_is_owner: bool = Form(False),
    source: str = Form(""),
    hide_author_show_source: bool = Form(False),
    time_limit_ms: int = Form(1000),
    memory_limit_kb: int = Form(262144),
    pids_limit: int = Form(64),
    output_limit_in_bytes: int = Form(65536),
    problem_statement: str = Form(""),
    category_ids: list[str] = Form(default=[]),
    return_page: str = Form("1"),
    return_per_page: str = Form("25"),
    return_search: str = Form(""),
    return_sort_by: str = Form("title_asc"),
    return_owner_id: str = Form(""),
    return_category_slugs: list[str] = Form(default=[]),
    next_url: str = Form(""),
    clear_image: bool = Form(False),
    image: UploadFile = File(None),
    image_caption: str = Form(""),
    notes: str = Form(""),
    license: str = Form(""),
    validator_language_id: str = Form(""),
    validator_source_file: UploadFile = File(None),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Submit updates to an existing Arena problem."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    safe_next = safe_next_path(next_url)
    back_url = problem_list_url(
        request,
        page=return_page,
        per_page=return_per_page,
        search=return_search,
        sort_by=return_sort_by,
        owner_id=return_owner_id,
        category_slugs=return_category_slugs,
        anchor=problem.id,
    )
    state = return_state(
        page=return_page,
        per_page=return_per_page,
        search=return_search,
        sort_by=return_sort_by,
        owner_id=return_owner_id,
        category_slugs=return_category_slugs,
    )
    form = form_fields(
        title=title,
        author=author,
        author_is_owner=author_is_owner,
        source=source,
        hide_author_show_source=hide_author_show_source,
        time_limit_ms=time_limit_ms,
        memory_limit_kb=memory_limit_kb,
        pids_limit=pids_limit,
        output_limit_in_bytes=output_limit_in_bytes,
        problem_statement=problem_statement,
        category_ids=category_ids,
        image_caption=image_caption,
        notes=notes,
        license=license,
    )

    async def render_error() -> HTMLResponse:
        test_cases, all_categories, problem_owner, has_submissions = await edit_form_extras(
            problem, current_user, session
        )
        return render_problem_form(
            request,
            mode="edit",
            problem=problem,
            test_cases=test_cases,
            form=form,
            cats_data=selected_cats_data(all_categories, category_ids),
            back_url=back_url,
            next_url=safe_next,
            state=state,
            problem_owner=problem_owner,
            has_submissions=has_submissions,
            validator_status=status_view(problem.custom_validator),
            validator_languages=await validator_languages(session),
            current_user=current_user,
            status_code=400,
        )

    # Check the validator upload before any database write, so a bad file leaves
    # the problem untouched.
    try:
        validator_upload = await parse_validator_upload(
            session,
            language_id=validator_language_id,
            source_file=validator_source_file,
        )
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return await render_error()

    image_b64: str | None = None
    image_mime: str | None = None
    if image and image.filename:
        try:
            image_b64, image_mime = await process_problem_image(request, image)
        except (ImageProcessingError, ValueError) as exc:
            flash(str(exc), FlashCategory.DANGER)
            return await render_error()

    try:
        await admin_problem_service.update_problem(
            session,
            problem,
            title=title,
            author=author or None,
            author_is_owner=author_is_owner,
            source=source or None,
            hide_author_show_source=hide_author_show_source,
            time_limit_ms=time_limit_ms,
            memory_limit_kb=memory_limit_kb,
            pids_limit=pids_limit,
            output_limit_in_bytes=output_limit_in_bytes,
            problem_statement=problem_statement,
            image_b64=image_b64,
            image_mime=image_mime,
            image_caption=image_caption or None,
            notes=notes or None,
            license=license or None,
            clear_image=clear_image,
            category_ids=category_ids,
        )
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return await render_error()

    form_data = await request.form()
    has_validator = validator_upload is not None or status_view(problem.custom_validator).configured
    try:
        cleanup_callbacks, file_writes = await admin_problem_tc_pending.apply_pending_testcases(
            session,
            problem,
            form_data,
            testcase_dir=settings.PROBLEM_TESTCASE_DIR,
            allow_empty=has_validator,
        )
    except ValueError as exc:
        flash(str(exc), FlashCategory.DANGER)
        return await render_error()

    # Stage the validator last: the all-samples rule must be judged against the
    # test cases this save actually leaves behind, not the ones it started with.
    validation_job = None
    if validator_upload is not None:
        try:
            validation_job = stage_candidate_revision(
                session,
                problem,
                validator_upload,
                test_cases=await admin_problem_tc_service.list_testcases(session, problem.id),
            )
        except ValueError as exc:
            flash(str(exc), FlashCategory.DANGER)
            return await render_error()

    await session.commit()
    for fn in cleanup_callbacks:
        await anyio.to_thread.run_sync(fn)
    for fn in file_writes:
        await anyio.to_thread.run_sync(fn)
    if validation_job is not None:
        await enqueue_custom_validator_validation_job(request.app.state.valkey_runtime, validation_job)
    flash(f"Problem #{problem.arena_number} updated.", FlashCategory.SUCCESS)
    redirect_target = safe_next or back_url
    return RedirectResponse(
        url=redirect_target,
        status_code=303,
    )


@router.post("/problems/{problem_id}/toggle-enabled", name="arena_admin_problem_toggle_enabled")
async def admin_problem_toggle_enabled(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    page: str = Query("1"),
    per_page: str = Query("25"),
    search: str = Query(""),
    sort_by: str = Query("title_asc"),
    owner_id: str = Query(""),
    category_slugs: list[str] | None = Query(None),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Toggle the enabled/disabled state of a problem."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    if not problem.enabled and not status_view(problem.custom_validator).usable:
        flash("Compile a valid custom validator before enabling this problem.", FlashCategory.DANGER)
        return RedirectResponse(request.url_for("arena_admin_problem_edit", problem_id=problem.id), 303)
    await admin_problem_service.toggle_enabled(session, problem)
    await session.commit()
    state = "enabled" if problem.enabled else "disabled"
    flash(f"Problem #{problem.arena_number} {state}.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=problem_list_url(
            request,
            page=page,
            per_page=per_page,
            search=search,
            sort_by=sort_by,
            owner_id=owner_id,
            category_slugs=category_slugs,
            anchor=problem.id,
        ),
        status_code=303,
    )


@router.post("/problems/{problem_id}/delete", name="arena_admin_problem_delete")
async def admin_problem_delete(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    password: str = Form(""),
    page: str = Form("1"),
    per_page: str = Form("25"),
    search: str = Form(""),
    sort_by: str = Form("title_asc"),
    owner_id: str = Form(""),
    category_slugs: list[str] = Form(default=[]),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Permanently delete a problem and all its dependent data."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    edit_url = str(request.url_for("arena_admin_problem_edit", problem_id=problem_id))

    if not current_user.check_password(password):
        flash("Incorrect password.", FlashCategory.DANGER)
        return RedirectResponse(url=edit_url, status_code=303)

    arena_number = await admin_problem_service.delete_problem(session, problem)
    await record_admin_action(
        session,
        request,
        module="arena",
        actor_user_id=current_user.id,
        action="delete",
        target_type="arena_problem",
        target_id=problem_id,
        detail=f"arena_number={arena_number}",
    )
    await session.commit()
    flash(f"Problem #{arena_number} deleted.", FlashCategory.SUCCESS)
    return RedirectResponse(
        url=problem_list_url(
            request,
            page=page,
            per_page=per_page,
            search=search,
            sort_by=sort_by,
            owner_id=owner_id,
            category_slugs=category_slugs,
        ),
        status_code=303,
    )


@router.post("/problems/{problem_id}/rejudge-all", name="arena_admin_problem_rejudge_all")
async def admin_problem_rejudge_all(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    password: str = Form(""),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Re-enqueue all existing submissions for a problem on the low-priority autojudge queue."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    edit_url = str(request.url_for("arena_admin_problem_edit", problem_id=problem_id))

    if not current_user.check_password(password):
        flash("Incorrect password.", FlashCategory.DANGER)
        return RedirectResponse(url=edit_url, status_code=303)

    jobs = await admin_problem_service.build_rejudge_jobs(session, problem.id)
    await session.commit()

    for job in jobs:
        await enqueue_arena_submission_job(request.app.state.valkey_runtime, job)

    count = len(jobs)
    flash(
        f"{count} submission{'s' if count != 1 else ''} enqueued for re-judging.",
        FlashCategory.SUCCESS,
    )
    return RedirectResponse(url=edit_url, status_code=303)
