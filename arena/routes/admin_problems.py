#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin routes for problem management (list, create, edit, toggle).

Presentation helpers (URL/context builders, form rendering) live in
``admin_problem_form_views.py``. Test-case sub-routes live in
``admin_problem_tc.py``; JSON API endpoints live in ``admin_problem_api.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_problem_editor
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_common import (
    get_problem_definition_or_403,
    get_problem_or_403,
    problem_enablement_error,
)
from arena.routes.admin_problem_form_views import (
    EDITORIAL_POLICY_COLORS,
    EDITORIAL_POLICY_ICONS,
    edit_form_extras,
    effective_per_page,
    form_fields,
    html_response,
    is_admin,
    parse_editorial_filter,
    parse_enabled_filter,
    problem_list_url,
    render_problem_form,
    return_state,
    safe_next_path,
    selected_cats_data,
)
from arena.routes.admin_problem_judgment_urls import judgment_page_url
from arena.routes.admin_problem_new import creation_return_query, resolve_choice_or_redirect
from arena.services import admin_problem_service
from arena.services.pagination_service import parse_page
from arena.services.statement_language_service import (
    safe_statement_language,
)
from shared.enumerations import ArenaEditorialReleasePolicy, StatementLanguage
from shared.services.admin_audit import record_admin_action
from shared.services.problem_definition_view import MOVED_TO_JUDGMENT
from shared.services.valkey_service.queue_ops import enqueue_arena_submission_job

router = APIRouter(prefix="/admin", tags=["arena-admin"])


def _effective_problem_sort(sort_by: str | None, search: str) -> str:
    """Return a valid admin problem sort with relevance as the search default."""
    default_sort = admin_problem_service.RELEVANCE_SORT if search.strip() else admin_problem_service.DEFAULT_SORT
    if sort_by not in admin_problem_service.VALID_SORTS:
        return default_sort
    if sort_by == admin_problem_service.RELEVANCE_SORT and not search.strip():
        return admin_problem_service.DEFAULT_SORT
    return sort_by


@router.get("/problems", response_class=HTMLResponse, name="arena_admin_problem_list")
async def admin_problem_list(
    request: Request,
    flash: FlashDep,
    page: str | None = None,
    per_page: str | None = None,
    search: str = "",
    sort_by: str | None = None,
    owner_id: str = "",
    category_slugs: list[str] | None = Query(None),
    language: str = "",
    enabled: str = "",
    editorial: str = "",
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the paginated Arena problem management list."""
    per_page_value = effective_per_page(per_page)
    is_adm = is_admin(current_user)
    effective_language = safe_statement_language(language)
    effective_sort = _effective_problem_sort(sort_by, search)
    effective_enabled = parse_enabled_filter(enabled)
    effective_editorial = parse_editorial_filter(editorial)

    pagination = await admin_problem_service.list_problems_paginated(
        session,
        page=parse_page(page),
        per_page=per_page_value,
        search=search,
        category_slugs=category_slugs or [],
        owner_id=owner_id if (is_adm and owner_id) else None,
        language=effective_language,
        enabled=effective_enabled,
        editorial=effective_editorial or None,
        sort_by=effective_sort,
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
                "sort_by": effective_sort,
                "sort_was_explicit": sort_by in admin_problem_service.VALID_SORTS,
                "selected_owner_id": owner_id,
                "selected_category_slugs": set(category_slugs or []),
                "language": effective_language.value if effective_language else "",
                "statement_languages": list(StatementLanguage),
                "selected_enabled": enabled if enabled in ("1", "0") else "",
                "selected_editorial": effective_editorial,
                "editorial_release_policies": list(ArenaEditorialReleasePolicy),
                "editorial_policy_icons": EDITORIAL_POLICY_ICONS,
                "editorial_policy_colors": EDITORIAL_POLICY_COLORS,
                "owners": owners,
                "all_categories": all_categories,
                "current_user": current_user,
                "is_admin": is_adm,
            },
        )
    )


@router.get("/problems/new/{validator_type}", response_class=HTMLResponse, name="arena_admin_problem_new")
async def admin_problem_new(
    request: Request,
    flash: FlashDep,
    validator_type: str,
    page: str = "1",
    per_page: str = "25",
    search: str = "",
    sort_by: str = admin_problem_service.DEFAULT_SORT,
    owner_id: str = "",
    category_slugs: list[str] | None = Query(None),
    language: str = "",
    enabled: str = "",
    editorial: str = "",
    next: str = Query(""),
    tab: str = Query(""),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the creation editor for one validation strategy.

    ``validator_type`` is taken as ``str`` and resolved in the handler on purpose:
    annotated as the enum, FastAPI answers ``422`` before the handler runs, which
    ``shared.error_handlers`` renders as a neutral JSON body -- wrong for an HTML
    admin page, and it would make an unknown strategy indistinguishable from the
    reserved one.
    """
    safe_next = safe_next_path(next)
    return_query = creation_return_query(
        page=page,
        per_page=per_page,
        search=search,
        sort_by=sort_by,
        owner_id=owner_id,
        category_slugs=category_slugs,
        language=language,
        enabled=enabled,
        editorial=editorial,
        next_path=safe_next,
    )
    strategy = resolve_choice_or_redirect(request, flash, validator_type, return_query)
    if isinstance(strategy, RedirectResponse):
        return strategy
    back_url = safe_next or problem_list_url(
        request,
        page=page,
        per_page=per_page,
        search=search,
        sort_by=sort_by,
        owner_id=owner_id,
        category_slugs=category_slugs,
        language=language,
        enabled=enabled,
        editorial=editorial,
    )
    return render_problem_form(
        request,
        mode="create",
        validator_type=strategy,
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
            editorial="",
            editorial_release_policy=ArenaEditorialReleasePolicy.NEVER.value,
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
            language=language,
            enabled=enabled,
            editorial=editorial,
        ),
        current_user=current_user,
        next_url=safe_next,
        active_tab=tab or None,
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
    sort_by: str = admin_problem_service.DEFAULT_SORT,
    owner_id: str = "",
    category_slugs: list[str] | None = Query(None),
    language: str = "",
    enabled: str = "",
    editorial: str = "",
    next: str = Query(""),
    tab: str = Query(""),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the problem definition editor.

    A ``?tab=`` naming a pane that moved to the judgment-data editor is redirected
    there rather than falling back to Metadata: an old link asking for test cases
    should land on test cases.
    """
    problem = await get_problem_definition_or_403(problem_id, current_user, session)
    moved = MOVED_TO_JUDGMENT.get(tab)
    if moved is not None:
        # The stale "?tab=" that named the moved pane has nothing to say on the
        # judgment page it moved to, so it is dropped rather than carried forward.
        return RedirectResponse(url=judgment_page_url(request, problem_id, moved, query=""), status_code=303)
    all_categories, problem_owner = await edit_form_extras(problem, current_user, session)
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
        language=language,
        enabled=enabled,
        editorial=editorial,
    )
    return render_problem_form(
        request,
        mode="edit",
        problem=problem,
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
            editorial=problem.editorial or "",
            editorial_release_policy=problem.editorial_release_policy.value,
            category_ids=selected_ids,
            image_caption=problem.problem_image_caption or "",
            notes=problem.notes or "",
            license=problem.license or "",
            statement_language=problem.statement_language.value if problem.statement_language else "",
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
            language=language,
            enabled=enabled,
            editorial=editorial,
        ),
        problem_owner=problem_owner,
        current_user=current_user,
        active_tab=tab or None,
    )


@router.post("/problems/{problem_id}/toggle-enabled", name="arena_admin_problem_toggle_enabled")
async def admin_problem_toggle_enabled(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    page: str = Query("1"),
    per_page: str = Query("25"),
    search: str = Query(""),
    sort_by: str = Query(admin_problem_service.DEFAULT_SORT),
    owner_id: str = Query(""),
    category_slugs: list[str] | None = Query(None),
    language: str = Query(""),
    enabled: str = Query(""),
    editorial: str = Query(""),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Toggle the enabled/disabled state of a problem."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    if not problem.enabled:
        # Enabling makes the problem visible and submittable, so it is one of the
        # execution gates where the shared judgeability contract applies.
        gate_error = await problem_enablement_error(session, problem)
        if gate_error is not None:
            flash(f"Cannot enable this problem. {gate_error}", FlashCategory.DANGER)
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
            language=language,
            enabled=enabled,
            editorial=editorial,
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
    sort_by: str = Form(admin_problem_service.DEFAULT_SORT),
    owner_id: str = Form(""),
    category_slugs: list[str] = Form(default=[]),
    language: str = Form(""),
    enabled: str = Form(""),
    editorial: str = Form(""),
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
        actor_label=current_user.email_normalizado,
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
            language=language,
            enabled=enabled,
            editorial=editorial,
        ),
        status_code=303,
    )


@router.post("/problems/{problem_id}/rejudge-all", name="arena_admin_problem_rejudge_all")
async def admin_problem_rejudge_all(
    request: Request,
    problem_id: str,
    flash: FlashDep,
    password: str = Form(""),
    next_url: str = Form(""),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Re-enqueue every submission and return to the workflow that requested it."""
    problem = await get_problem_or_403(problem_id, current_user, session)
    edit_url = str(request.url_for("arena_admin_problem_edit", problem_id=problem_id))
    return_url = safe_next_path(next_url) or edit_url

    if not current_user.check_password(password):
        flash("Incorrect password.", FlashCategory.DANGER)
        return RedirectResponse(url=return_url, status_code=303)

    jobs = await admin_problem_service.build_rejudge_jobs(session, problem.id)
    await session.commit()

    for job in jobs:
        await enqueue_arena_submission_job(request.app.state.valkey_runtime, job)

    count = len(jobs)
    flash(
        f"{count} submission{'s' if count != 1 else ''} enqueued for re-judging.",
        FlashCategory.SUCCESS,
    )
    return RedirectResponse(url=return_url, status_code=303)
