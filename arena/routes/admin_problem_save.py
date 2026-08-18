#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The Arena problem definition editor's create and update endpoints.

These handlers save metadata, the statement, editorial, illustration, and categories.
Test cases, the custom validator, and sample interactions belong to the separate
judgment-data pages and never ride this form's Save.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_problem_editor
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_common import (
    get_problem_definition_or_403,
    problem_enablement_error,
)
from arena.routes.admin_problem_form_views import (
    edit_form_extras,
    form_fields,
    problem_list_url,
    process_problem_image,
    render_problem_form,
    return_state,
    safe_next_path,
    selected_cats_data,
)
from arena.routes.admin_problem_judgment_urls import judgment_page_url
from arena.routes.admin_problem_new import resolve_choice_or_redirect
from arena.services import admin_problem_service
from arena.services.statement_language_service import (
    LanguageConflict,
    conflict_context,
    resolve_statement_language,
)
from shared.enumerations import ArenaEditorialReleasePolicy, ProblemValidatorType
from shared.http_params import PG_INT32_MAX
from shared.services.imageprocessing_service import ImageProcessingError
from shared.services.problem_definition_view import TAB_EDITORIAL, TAB_METADATA, TAB_STATEMENT
from shared.services.problem_editor_save import (
    lock_problem_row,
)

router = APIRouter(prefix="/admin", tags=["arena-admin-problems"])


def _parse_resource_limits(
    time_limit_ms: str,
    memory_limit_kb: str,
    pids_limit: str,
    output_limit_in_bytes: str,
) -> tuple[tuple[int, int, int, int] | None, dict[str, str]]:
    """Parse all Arena resource limits without falling into FastAPI's JSON 422."""
    raw_fields = (
        ("time_limit_ms", "Time limit (ms)", time_limit_ms),
        ("memory_limit_kb", "Memory limit (KB)", memory_limit_kb),
        ("pids_limit", "PIDs limit", pids_limit),
        ("output_limit_in_bytes", "Output limit (bytes)", output_limit_in_bytes),
    )
    values: list[int] = []
    errors: dict[str, str] = {}
    for field, label, raw in raw_fields:
        try:
            value = int(raw)
        except ValueError:
            errors[field] = f"{label} must be a whole number."
            continue
        if value < 1:
            errors[field] = f"{label} must be at least 1."
        elif value > PG_INT32_MAX:
            errors[field] = f"{label} must be at most {PG_INT32_MAX}."
        else:
            values.append(value)
    if errors:
        return None, errors
    return (values[0], values[1], values[2], values[3]), {}


def _problem_error_field(message: str) -> str:
    """Map a scalar service error back to the form field that owns it."""
    prefixes = {
        "Title": "title",
        "Author": "author",
        "Time limit": "time_limit_ms",
        "Memory limit": "memory_limit_kb",
        "PIDs limit": "pids_limit",
        "Output limit": "output_limit_in_bytes",
        "Markdown statement": "problem_statement",
        "Editorial": "editorial",
    }
    return next((field for prefix, field in prefixes.items() if message.startswith(prefix)), "")


def _problem_error_tab(field: str) -> str:
    """Return the definition tab that owns a problem service error field."""
    if field == "problem_statement":
        return TAB_STATEMENT
    if field == "editorial":
        return TAB_EDITORIAL
    return TAB_METADATA


@router.post("/problems/new/{validator_type}", name="arena_admin_problem_create")
async def admin_problem_create(
    request: Request,
    flash: FlashDep,
    validator_type: str,
    title: str = Form(""),
    author: str = Form(""),
    author_is_owner: bool = Form(False),
    source: str = Form(""),
    hide_author_show_source: bool = Form(False),
    time_limit_ms: str = Form("1000"),
    memory_limit_kb: str = Form("262144"),
    pids_limit: str = Form("64"),
    output_limit_in_bytes: str = Form("65536"),
    problem_statement: str = Form(""),
    editorial: str = Form(""),
    editorial_release_policy: str = Form(ArenaEditorialReleasePolicy.NEVER.value),
    category_ids: list[str] = Form(default=[]),
    return_page: str = Form("1"),
    return_per_page: str = Form("25"),
    return_search: str = Form(""),
    return_sort_by: str = Form(admin_problem_service.DEFAULT_SORT),
    return_owner_id: str = Form(""),
    return_category_slugs: list[str] = Form(default=[]),
    return_language: str = Form(""),
    return_enabled: str = Form(""),
    return_editorial: str = Form(""),
    image: UploadFile = File(None),
    image_caption: str = Form(""),
    notes: str = Form(""),
    license: str = Form(""),
    statement_language: str = Form(""),
    language_confirmed: str = Form(""),
    active_tab: str = Form(""),
    next_url: str = Form(""),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Create a new Arena problem (always starts as disabled).

    The strategy comes from the validated route parameter and from nowhere else:
    any ``validator_type`` a crafted POST body carries is never read, so form
    tampering cannot select or change one.
    """
    strategy = resolve_choice_or_redirect(request, flash, validator_type)
    if isinstance(strategy, RedirectResponse):
        return strategy
    interactive = strategy is ProblemValidatorType.INTERACTIVE
    safe_next = safe_next_path(next_url)
    back_url = safe_next or problem_list_url(
        request,
        page=return_page,
        per_page=return_per_page,
        search=return_search,
        sort_by=return_sort_by,
        owner_id=return_owner_id,
        category_slugs=return_category_slugs,
        language=return_language,
        enabled=return_enabled,
        editorial=return_editorial,
    )
    state = return_state(
        page=return_page,
        per_page=return_per_page,
        search=return_search,
        sort_by=return_sort_by,
        owner_id=return_owner_id,
        category_slugs=return_category_slugs,
        language=return_language,
        enabled=return_enabled,
        editorial=return_editorial,
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
        editorial=editorial,
        editorial_release_policy=editorial_release_policy,
        category_ids=category_ids,
        image_caption=image_caption,
        notes=notes,
        license=license,
        statement_language=statement_language,
    )

    async def render_error(
        language_conflict: dict[str, str] | None = None,
        *,
        errors: tuple[str, ...] = (),
        field_errors: dict[str, str] | None = None,
        error_tab: str | None = None,
    ) -> HTMLResponse:
        all_categories = await admin_problem_service.search_categories(session, query="", limit=200)
        return render_problem_form(
            request,
            mode="create",
            validator_type=strategy,
            form=form,
            cats_data=selected_cats_data(all_categories, category_ids),
            back_url=back_url,
            state=state,
            current_user=current_user,
            language_conflict=language_conflict,
            next_url=safe_next,
            active_tab=error_tab or active_tab or None,
            errors=errors,
            field_errors=field_errors,
            status_code=422,
        )

    parsed_limits, limit_errors = _parse_resource_limits(
        time_limit_ms,
        memory_limit_kb,
        pids_limit,
        output_limit_in_bytes,
    )
    if limit_errors:
        return await render_error(
            errors=tuple(limit_errors.values()),
            field_errors=limit_errors,
            error_tab="metadata",
        )
    assert parsed_limits is not None
    time_limit_value, memory_limit_value, pids_limit_value, output_limit_value = parsed_limits

    # A stated language that disagrees with detection must be acknowledged before
    # anything is written, so a mistyped language never reaches the database.
    try:
        resolution = await resolve_statement_language(
            chosen_raw=statement_language,
            confirmed_raw=language_confirmed,
            statement=problem_statement,
            title=title,
        )
    except ValueError as exc:
        return await render_error(errors=(str(exc),), error_tab="metadata")
    if isinstance(resolution, LanguageConflict):
        return await render_error(
            language_conflict=conflict_context(resolution),
            error_tab="metadata",
        )
    resolved_language = resolution.language

    try:
        resolved_editorial_release_policy = ArenaEditorialReleasePolicy(editorial_release_policy)
    except ValueError:
        return await render_error(
            errors=("Choose a valid editorial release policy.",),
            field_errors={"editorial_release_policy": "Choose a valid editorial release policy."},
            error_tab=TAB_EDITORIAL,
        )

    # Creation collects the problem *definition* only. Test cases, the custom
    # validator and sample interactions are authored on the judgment-data pages,
    # which this route redirects to on success -- so a form field naming any of
    # them is never read here.

    image_b64: str | None = None
    image_mime: str | None = None
    if image and image.filename:
        try:
            image_b64, image_mime = await process_problem_image(request, image)
        except (ImageProcessingError, ValueError) as exc:
            return await render_error(errors=(str(exc),), error_tab="statement")

    try:
        problem = await admin_problem_service.create_problem(
            session,
            caller_id=current_user.id,
            title=title,
            author=author or None,
            author_is_owner=author_is_owner,
            source=source or None,
            hide_author_show_source=hide_author_show_source,
            time_limit_ms=time_limit_value,
            memory_limit_kb=memory_limit_value,
            pids_limit=pids_limit_value,
            output_limit_in_bytes=output_limit_value,
            problem_statement=problem_statement,
            editorial=editorial,
            editorial_release_policy=resolved_editorial_release_policy,
            image_b64=image_b64,
            image_mime=image_mime,
            image_caption=image_caption or None,
            notes=notes or None,
            license=license or None,
            category_ids=category_ids,
            statement_language=resolved_language,
            validator_type=strategy,
        )
    except ValueError as exc:
        message = str(exc)
        field = _problem_error_field(message)
        return await render_error(
            errors=(message,),
            field_errors={field: message} if field else None,
            error_tab=_problem_error_tab(field),
        )

    # An Arena problem definition is entirely database columns -- the statement is
    # a text column, not a file -- and a new problem has no test cases yet. So this
    # create writes nothing to the filesystem and needs no staged swap: there is no
    # second system for a failed commit to leave inconsistent.
    await session.commit()
    flash(f"Problem #{problem.arena_number} created (disabled).", FlashCategory.SUCCESS)
    if safe_next:
        return RedirectResponse(url=safe_next, status_code=303)
    # An interactive problem cannot be judged at all until a validator compiles, so
    # creation lands on that page; everything else starts at its test cases.
    return RedirectResponse(
        url=judgment_page_url(request, problem.id, "validator" if interactive else "test-cases"),
        status_code=303,
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
    time_limit_ms: str = Form("1000"),
    memory_limit_kb: str = Form("262144"),
    pids_limit: str = Form("64"),
    output_limit_in_bytes: str = Form("65536"),
    problem_statement: str = Form(""),
    editorial: str = Form(""),
    editorial_release_policy: str = Form(ArenaEditorialReleasePolicy.NEVER.value),
    category_ids: list[str] = Form(default=[]),
    return_page: str = Form("1"),
    return_per_page: str = Form("25"),
    return_search: str = Form(""),
    return_sort_by: str = Form(admin_problem_service.DEFAULT_SORT),
    return_owner_id: str = Form(""),
    return_category_slugs: list[str] = Form(default=[]),
    return_language: str = Form(""),
    return_enabled: str = Form(""),
    return_editorial: str = Form(""),
    next_url: str = Form(""),
    clear_image: bool = Form(False),
    image: UploadFile = File(None),
    image_caption: str = Form(""),
    notes: str = Form(""),
    license: str = Form(""),
    statement_language: str = Form(""),
    language_confirmed: str = Form(""),
    active_tab: str = Form(""),
    save_action: str = Form(""),
    current_user: ArenaUser = Depends(require_arena_problem_editor),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Save what the problem *is*: metadata, statement, editorial, illustration, categories.

    What judging runs against -- test cases, the validator, sample interactions --
    is edited on its own pages and applies as it is clicked. This save briefly
    carried all of it, which meant one form held a problem's entire test data.
    """
    problem = await get_problem_definition_or_403(problem_id, current_user, session)
    # Serialize against another save of the same problem *before* its test cases
    # are read: two saves that both snapshot the live directory would each stage a
    # complete replacement, and the loser would quietly reinstate what the winner
    # replaced.
    if not await lock_problem_row(session, "arena", problem.id):
        raise HTTPException(status_code=404, detail="Problem not found")
    safe_next = safe_next_path(next_url)
    back_url = problem_list_url(
        request,
        page=return_page,
        per_page=return_per_page,
        search=return_search,
        sort_by=return_sort_by,
        owner_id=return_owner_id,
        category_slugs=return_category_slugs,
        language=return_language,
        enabled=return_enabled,
        editorial=return_editorial,
        anchor=problem.id,
    )
    state = return_state(
        page=return_page,
        per_page=return_per_page,
        search=return_search,
        sort_by=return_sort_by,
        owner_id=return_owner_id,
        category_slugs=return_category_slugs,
        language=return_language,
        enabled=return_enabled,
        editorial=return_editorial,
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
        editorial=editorial,
        editorial_release_policy=editorial_release_policy,
        category_ids=category_ids,
        image_caption=image_caption,
        notes=notes,
        license=license,
        statement_language=statement_language,
    )

    async def render_error(
        language_conflict: dict[str, str] | None = None,
        *,
        errors: tuple[str, ...] = (),
        field_errors: dict[str, str] | None = None,
        error_tab: str | None = None,
    ) -> HTMLResponse:
        all_categories, problem_owner = await edit_form_extras(problem, current_user, session)
        return render_problem_form(
            request,
            mode="edit",
            problem=problem,
            form=form,
            cats_data=selected_cats_data(all_categories, category_ids),
            back_url=back_url,
            next_url=safe_next,
            state=state,
            problem_owner=problem_owner,
            current_user=current_user,
            language_conflict=language_conflict,
            save_action=save_action,
            active_tab=error_tab or active_tab or None,
            errors=errors,
            field_errors=field_errors,
            reselect_uploads=("the problem illustration",) if image and image.filename else (),
            status_code=422,
        )

    if save_action not in {"enable", "disable"}:
        return await render_error(
            errors=("Choose Save and enable or Save and disable.",),
            error_tab=active_tab or "metadata",
        )

    parsed_limits, limit_errors = _parse_resource_limits(
        time_limit_ms,
        memory_limit_kb,
        pids_limit,
        output_limit_in_bytes,
    )
    if limit_errors:
        return await render_error(
            errors=tuple(limit_errors.values()),
            field_errors=limit_errors,
            error_tab="metadata",
        )
    assert parsed_limits is not None
    time_limit_value, memory_limit_value, pids_limit_value, output_limit_value = parsed_limits

    # A stated language that disagrees with detection must be acknowledged before
    # anything is written, so a mistyped language never reaches the database.
    try:
        resolution = await resolve_statement_language(
            chosen_raw=statement_language,
            confirmed_raw=language_confirmed,
            statement=problem_statement,
            title=title,
        )
    except ValueError as exc:
        return await render_error(errors=(str(exc),), error_tab="metadata")
    if isinstance(resolution, LanguageConflict):
        return await render_error(
            language_conflict=conflict_context(resolution),
            error_tab="metadata",
        )
    resolved_language = resolution.language

    try:
        resolved_editorial_release_policy = ArenaEditorialReleasePolicy(editorial_release_policy)
    except ValueError:
        return await render_error(
            errors=("Choose a valid editorial release policy.",),
            field_errors={"editorial_release_policy": "Choose a valid editorial release policy."},
            error_tab=TAB_EDITORIAL,
        )

    image_b64: str | None = None
    image_mime: str | None = None
    if image and image.filename:
        try:
            image_b64, image_mime = await process_problem_image(request, image)
        except (ImageProcessingError, ValueError) as exc:
            return await render_error(errors=(str(exc),), error_tab="statement")

    try:
        await admin_problem_service.update_problem(
            session,
            problem,
            title=title,
            author=author or None,
            author_is_owner=author_is_owner,
            source=source or None,
            hide_author_show_source=hide_author_show_source,
            time_limit_ms=time_limit_value,
            memory_limit_kb=memory_limit_value,
            pids_limit=pids_limit_value,
            output_limit_in_bytes=output_limit_value,
            problem_statement=problem_statement,
            editorial=editorial,
            editorial_release_policy=resolved_editorial_release_policy,
            image_b64=image_b64,
            image_mime=image_mime,
            image_caption=image_caption or None,
            notes=notes or None,
            license=license or None,
            clear_image=clear_image,
            category_ids=category_ids,
            statement_language=resolved_language,
        )
    except ValueError as exc:
        message = str(exc)
        field = _problem_error_field(message)
        return await render_error(
            errors=(message,),
            field_errors={field: message} if field else None,
            error_tab=_problem_error_tab(field),
        )

    # ``save_action`` names the *target* state rather than toggling, so the gate
    # applies whenever that target is "enabled" -- not only on the disabled to
    # enabled transition. A problem can lose its last test case while enabled
    # (removing it is allowed and leaves an incomplete draft), and gating the
    # transition alone would let that Save keep an unjudgeable problem published.
    # "Save and disable" stays ungated, so a broken problem can still be edited.
    enable = save_action == "enable"
    if enable:
        gate_error = await problem_enablement_error(session, problem)
        if gate_error is not None:
            return await render_error(
                errors=(f"Cannot enable this problem. {gate_error}",),
                error_tab=active_tab or "metadata",
            )
    problem.enabled = enable

    # Arena statements are Markdown in the database, so this save writes no file
    # at all: there is nothing to stage, and it commits directly.
    await session.commit()
    publication_state = "enabled" if problem.enabled else "disabled"
    flash(
        f"Problem #{problem.arena_number} updated and {publication_state}.",
        FlashCategory.SUCCESS,
    )
    return RedirectResponse(url=safe_next or back_url, status_code=303)
