#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Presentation helpers for the Arena admin problem create/edit form.

These helpers build the URL, context dicts, and rendered responses shared by the
list, create, and edit routes in ``admin_problems.py``. They hold no routing or
business logic so the route handlers stay focused on request/response flow.
"""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import quote, urlencode

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_problems import ArenaProblem, ArenaSampleInteraction
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_judgment_urls import with_query
from arena.services import admin_problem_service
from arena.services.admin_problem_tc_service import TestCaseView
from shared.enumerations import (
    ArenaEditorialReleasePolicy,
    ArenaExpectedDifficulty,
    ArenaRole,
    ProblemValidatorType,
    StatementLanguage,
)
from shared.services.arena_difficulty_display import MIN_ATTEMPTS_FOR_DISPLAY
from shared.services.form_draft import problem_definition_draft_key
from shared.services.imageprocessing_service import ImageProcessingService
from shared.services.problem_definition_view import (
    TAB_EDITORIAL,
    TAB_METADATA,
    TAB_STATEMENT,
    ProblemDefinitionView,
    label_for_strategy,
    resolve_tab,
)
from shared.services.problem_editor_header import (
    EditorAction,
    EditorLink,
    ProblemEditorHeaderView,
    publish_state_actions,
)
from shared.services.problem_image import process_problem_image_upload
from shared.services.sample_interactions import (
    SampleInteractionRowView,
    transcript_line_count,
    transcript_preview,
)
from shared.services.testcase_view import TestCaseRowView

ALLOWED_PER_PAGE = [10, 25, 50, 100, 500]
DEFAULT_PER_PAGE = 25


def html_response(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def is_admin(user: ArenaUser) -> bool:
    """Return whether the user holds the ARENA_ADMIN role."""
    return user.role == ArenaRole.ARENA_ADMIN


def build_testcase_row_views(
    request: Request,
    problem_id: str,
    views: list[TestCaseView],
) -> list[TestCaseRowView]:
    """Adapt Arena ``TestCaseView`` items into shared list-partial view models.

    URLs are pre-built with the Arena route names so the shared template never
    resolves module-specific ``url_for`` names. The current request's query
    string -- the problem list's page, filters, and sort -- rides along on every
    row action so a save or delete that redirects back to this page does not
    lose it either.
    """
    query = request.url.query
    rows: list[TestCaseRowView] = []
    for tc in views:
        rows.append(
            TestCaseRowView(
                id=tc.id,
                ordinal=tc.ordinal,
                is_sample=tc.is_sample,
                has_explanation=tc.has_explanation,
                input_preview=tc.input_preview,
                output_preview=tc.output_preview,
                input_size_bytes=tc.input_size_bytes,
                output_size_bytes=tc.output_size_bytes,
                is_large=tc.is_large,
                edit_url=with_query(
                    str(request.url_for("arena_admin_problem_tc_edit", problem_id=problem_id, tc_id=tc.id)), query
                ),
                download_url=str(
                    request.url_for("arena_admin_problem_tc_download", problem_id=problem_id, tc_id=tc.id)
                ),
                replace_url=with_query(
                    str(
                        request.url_for("arena_admin_problem_judgment_case_replace", problem_id=problem_id, tc_id=tc.id)
                    ),
                    query,
                ),
                move_url=with_query(
                    str(request.url_for("arena_admin_problem_tc_move", problem_id=problem_id, tc_id=tc.id)), query
                ),
                toggle_sample_url=with_query(
                    str(
                        request.url_for(
                            "arena_admin_problem_judgment_case_toggle_sample", problem_id=problem_id, tc_id=tc.id
                        )
                    ),
                    query,
                ),
                delete_url=with_query(
                    str(
                        request.url_for("arena_admin_problem_judgment_case_delete", problem_id=problem_id, tc_id=tc.id)
                    ),
                    query,
                ),
            )
        )
    return rows


def build_interaction_row_views(
    request: Request,
    problem_id: str,
    interactions: list[ArenaSampleInteraction],
) -> list[SampleInteractionRowView]:
    """Adapt Arena sample-interaction rows into shared list-partial view models.

    URLs are pre-built with the Arena route names so the shared template never
    resolves module-specific ``url_for`` names. The current request's query
    string rides along on every row action for the same reason it does on the
    test-case rows.
    """
    query = request.url.query
    return [
        SampleInteractionRowView(
            id=interaction.id,
            ordinal=interaction.ordinal,
            preview=transcript_preview(interaction.transcript),
            line_count=transcript_line_count(interaction.transcript),
            has_explanation=bool(interaction.explanation),
            edit_url=with_query(
                str(
                    request.url_for("arena_admin_problem_interaction_edit", problem_id=problem_id, si_id=interaction.id)
                ),
                query,
            ),
            move_url=with_query(
                str(
                    request.url_for("arena_admin_problem_interaction_move", problem_id=problem_id, si_id=interaction.id)
                ),
                query,
            ),
            delete_url=with_query(
                str(
                    request.url_for(
                        "arena_admin_problem_judgment_interaction_delete",
                        problem_id=problem_id,
                        si_id=interaction.id,
                    )
                ),
                query,
            ),
        )
        for interaction in sorted(interactions, key=lambda item: item.ordinal)
    ]


def effective_per_page(value: str | None) -> int:
    """Return an allowed page size, falling back to the default."""
    try:
        effective = int(value) if value else DEFAULT_PER_PAGE
    except TypeError, ValueError:
        return DEFAULT_PER_PAGE
    return effective if effective in ALLOWED_PER_PAGE else DEFAULT_PER_PAGE


def parse_enabled_filter(value: str) -> bool | None:
    """Return the tri-state enabled/disabled filter as ``bool | None`` (None = all)."""
    if value == "1":
        return True
    if value == "0":
        return False
    return None


_EDITORIAL_FILTER_VALUES = frozenset({"none", "never", "always", "after_ac"})


def parse_editorial_filter(value: str) -> str:
    """Return a valid editorial-filter value, or ``""`` when unrecognized (= all)."""
    return value if value in _EDITORIAL_FILTER_VALUES else ""


#: Maps each release policy value to the Material Symbols icon and Bootstrap
#: text color shown on the problem list, so the mapping lives in one place
#: instead of the template.
EDITORIAL_POLICY_ICONS: dict[str, str] = {
    ArenaEditorialReleasePolicy.NEVER.value: "block",
    ArenaEditorialReleasePolicy.ALWAYS.value: "public",
    ArenaEditorialReleasePolicy.AFTER_AC.value: "task_alt",
}

EDITORIAL_POLICY_COLORS: dict[str, str] = {
    ArenaEditorialReleasePolicy.NEVER.value: "text-danger",
    ArenaEditorialReleasePolicy.ALWAYS.value: "text-success",
    ArenaEditorialReleasePolicy.AFTER_AC.value: "text-info",
}


def safe_next_path(next_url: str | None) -> str:
    """Return a same-origin path-only next URL, or an empty string."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return ""


def problem_list_url(
    request: Request,
    *,
    page: str = "1",
    per_page: str = "25",
    search: str = "",
    sort_by: str = admin_problem_service.DEFAULT_SORT,
    owner_id: str = "",
    category_slugs: list[str] | None = None,
    language: str = "",
    enabled: str = "",
    editorial: str = "",
    anchor: str | None = None,
) -> str:
    """Build a problem list URL preserving non-default filter/sort state."""
    params: dict[str, str] = {}
    if page and page != "1":
        params["page"] = page
    if per_page and per_page != str(DEFAULT_PER_PAGE):
        params["per_page"] = per_page
    if search:
        params["search"] = search
    if sort_by and sort_by != admin_problem_service.DEFAULT_SORT:
        params["sort_by"] = sort_by
    if owner_id:
        params["owner_id"] = owner_id
    if language:
        params["language"] = language
    if enabled:
        params["enabled"] = enabled
    if editorial:
        params["editorial"] = editorial
    qs_parts = urlencode(params)
    category_qs = urlencode({"category_slugs": category_slugs or []}, doseq=True)
    query_parts = [part for part in (qs_parts, category_qs) if part]
    base_url = str(request.url_for("arena_admin_problem_list"))
    url = f"{base_url}?{'&'.join(query_parts)}" if query_parts else base_url
    return f"{url}#{quote(anchor, safe='')}" if anchor else url


def selected_cats_data(all_categories: list[Any], category_ids: list[str]) -> list[dict[str, str]]:
    """Build the list of category dicts used to pre-populate the JS tag-picker.

    Args:
        all_categories: Full list of ``ArenaCategory`` objects from the DB.
        category_ids: IDs currently selected (from a form submission or saved problem).

    Returns:
        list[dict]: Each dict has ``id``, ``name``, ``color``, ``foreground_color``.
    """
    id_set = set(category_ids)
    return [
        {
            "id": c.id,
            "name": c.name,
            "color": c.color,
            "foreground_color": c.foreground_color,
        }
        for c in all_categories
        if c.id in id_set
    ]


async def process_problem_image(request: Request, image: UploadFile) -> tuple[str, str]:
    """Process an uploaded problem illustration into a ``(base64, mime)`` pair."""
    image_service: ImageProcessingService = request.app.state.image_service
    return await process_problem_image_upload(image_service, image)


def form_fields(
    *,
    title: str,
    author: str,
    author_is_owner: bool,
    source: str,
    hide_author_show_source: bool,
    time_limit_ms: int | str,
    memory_limit_kb: int | str,
    pids_limit: int | str,
    output_limit_in_bytes: int | str,
    problem_statement: str,
    editorial: str,
    category_ids: list[str],
    image_caption: str,
    notes: str = "",
    license: str = "",
    statement_language: str = "",
    editorial_release_policy: str = ArenaEditorialReleasePolicy.NEVER.value,
    expected_difficulty: str = "",
) -> dict[str, Any]:
    """Build the form context, retaining raw limit strings after rejection."""
    return {
        "title": title,
        "author": author,
        "author_is_owner": author_is_owner,
        "source": source,
        "hide_author_show_source": hide_author_show_source,
        "time_limit_ms": time_limit_ms,
        "memory_limit_kb": memory_limit_kb,
        "pids_limit": pids_limit,
        "output_limit_in_bytes": output_limit_in_bytes,
        "problem_statement": problem_statement,
        "editorial": editorial,
        "category_ids": category_ids,
        "image_caption": image_caption,
        "notes": notes,
        "license": license,
        "statement_language": statement_language,
        "editorial_release_policy": editorial_release_policy,
        "expected_difficulty": expected_difficulty,
    }


def _off_anchor_difficulty(problem: ArenaProblem | None) -> dict[str, Any] | None:
    """Describe a stored expected difficulty that is not one of the worded anchors.

    An imported package may carry any value in ``[1, 100]``. Without an option of
    its own such a value matches nothing in the select, so the browser shows the
    first option ("No estimate") and the next save of any other field wipes it.
    The form renders this as an extra option instead.

    Returns:
        The value and its display-scale form, or ``None`` when the problem has no
        estimate or its estimate is an anchor the select already offers.
    """
    stored = problem.expected_difficulty if problem is not None else None
    if stored is None or ArenaExpectedDifficulty.from_internal(stored) is not None:
        return None
    return {"value": str(stored), "display_value": stored / 10.0}


def parse_expected_difficulty(raw: str, *, current: int | None = None) -> int | None:
    """Parse the expected-difficulty form value into an internal rating, or ``None``.

    The form offers the worded anchors of ``ArenaExpectedDifficulty``; an empty
    value means "no estimate". Any other value is refused, so a tampered or
    stale option can never store a number the author did not choose.

    The column stores any integer in ``[1, 100]``, so an imported package may
    carry a value that is not an anchor. ``current`` is that stored value, which
    the form renders as an extra selected option so an author editing an
    unrelated field keeps it instead of silently discarding it. Accepting
    exactly that value -- and nothing else off the anchor list -- is what keeps
    the option from being a hole in the vocabulary.

    Args:
        raw: The submitted form value.
        current: The value already stored on the problem being edited, if any.

    Raises:
        ValueError: When the value is neither empty, an anchor, nor ``current``.
    """
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        parsed = int(cleaned)
    except ValueError:
        raise ValueError("Choose a valid expected difficulty.") from None
    if current is not None and parsed == current:
        return parsed
    try:
        return int(ArenaExpectedDifficulty(parsed))
    except ValueError:
        raise ValueError("Choose a valid expected difficulty.") from None


def return_state(
    *,
    page: str,
    per_page: str,
    search: str,
    sort_by: str,
    owner_id: str,
    category_slugs: list[str] | None,
    language: str = "",
    enabled: str = "",
    editorial: str = "",
) -> dict[str, Any]:
    """Build the hidden return-state dict that preserves list filters across the form."""
    return {
        "page": page,
        "per_page": per_page,
        "search": search,
        "sort_by": sort_by,
        "owner_id": owner_id,
        "category_slugs": category_slugs or [],
        "language": language,
        "enabled": enabled,
        "editorial": editorial,
    }


#: Panes the Arena definition editor renders. Arena keeps its resource limits
#: inside Metadata, so it has no Limits pane and must not accept ``?tab=limits``.
ARENA_EDITOR_TABS: tuple[str, ...] = (TAB_METADATA, TAB_STATEMENT, TAB_EDITORIAL)


def build_problem_form_view(
    request: Request,
    *,
    problem: ArenaProblem | None,
    validator_type: ProblemValidatorType,
    back_url: str,
    active_tab: str | None,
    reselect_uploads: tuple[str, ...] = (),
) -> ProblemDefinitionView:
    """Build the shared editor view model for one Arena problem-form rendering.

    All URLs are resolved here so the shared tab partials never resolve Arena
    route names or Arena template paths.

    Args:
        request: The active request.
        problem: The problem being edited, or None while creating.
        validator_type: The problem's stored (or chosen) strategy.
        back_url: Where Cancel/Back returns to.
        active_tab: Requested tab, from ``?tab=`` or an ``active_tab`` field.
        reselect_uploads: Labels of archives a rejected submission carried, which
            the browser cannot restore and the author must choose again.

    Returns:
        ProblemDefinitionView: The resolved view model.
    """
    is_interactive = validator_type is ProblemValidatorType.INTERACTIVE
    tabs = ARENA_EDITOR_TABS
    resolved_tab = resolve_tab(active_tab, allowed=frozenset(tabs))
    draft_key = problem_definition_draft_key(
        "arena",
        problem_id=problem.id if problem is not None else None,
        validator_type=validator_type.value,
    )
    if problem is None:
        return ProblemDefinitionView(
            validator_type=validator_type,
            is_interactive=is_interactive,
            is_create=True,
            header=ProblemEditorHeaderView(
                title="New problem",
                form_id="edit-form",
                actions=(EditorAction(label="Create problem", icon="check", value=""),),
                back_url=back_url,
                strategy_label=label_for_strategy(validator_type),
            ),
            allow_pdf=False,
            tabs=tabs,
            active_tab=resolved_tab,
            save_url=str(request.url_for("arena_admin_problem_create", validator_type=validator_type.value)),
            cancel_url=back_url,
            # A problem must exist before it can have judgment data.
            judgment_url="",
            reselect_uploads=reselect_uploads,
            draft_key=draft_key,
        )
    problem_id = problem.id
    judgment_url = with_query(
        str(request.url_for("arena_admin_problem_judgment", problem_id=problem_id)), request.url.query
    )
    return ProblemDefinitionView(
        validator_type=validator_type,
        is_interactive=is_interactive,
        is_create=False,
        header=ProblemEditorHeaderView(
            title=f"Edit problem #{problem.arena_number}",
            form_id="edit-form",
            subtitle=problem.title,
            actions=publish_state_actions(),
            back_url=back_url,
            links=(
                EditorLink(label="Judgment data", url=judgment_url, icon="rule"),
                EditorLink(
                    label="Download problem package",
                    url=str(request.url_for("arena_admin_problem_export", problem_id=problem_id)),
                    icon="download",
                    icon_only=True,
                ),
            ),
            strategy_label=label_for_strategy(validator_type),
        ),
        allow_pdf=False,
        tabs=tabs,
        active_tab=resolved_tab,
        save_url=str(request.url_for("arena_admin_problem_update", problem_id=problem_id)),
        cancel_url=back_url,
        editor_base_url=str(request.url_for("arena_admin_problem_edit", problem_id=problem_id)),
        judgment_url=judgment_url,
        validator_status_template="admin/_validator_status.html",
        reselect_uploads=reselect_uploads,
        draft_key=draft_key,
    )


def render_problem_form(
    request: Request,
    *,
    mode: str,
    form: dict[str, Any],
    cats_data: list[dict[str, str]],
    back_url: str,
    state: dict[str, Any],
    current_user: ArenaUser,
    validator_type: ProblemValidatorType | None = None,
    problem: ArenaProblem | None = None,
    next_url: str | None = None,
    problem_owner: ArenaUser | None = None,
    language_conflict: dict[str, str] | None = None,
    save_action: str = "",
    active_tab: str | None = None,
    errors: tuple[str, ...] = (),
    field_errors: dict[str, str] | None = None,
    reselect_uploads: tuple[str, ...] = (),
    status_code: int = 200,
) -> HTMLResponse:
    """Render ``problem_form.html`` with the shared create/edit context.

    Edit-only context keys (``next_url``, ``problem_owner``, ``rating_history_url``)
    are added only when ``mode == "edit"``.

    ``validator_type`` is required in create mode: it is part of the create POST
    path, so the template's form action cannot be built without it. In edit mode
    it is read from the stored problem instead.

    ``reselect_uploads`` names the file inputs a rejected submission carried: the
    browser cannot restore them, so the author is told which to choose again.

    ``field_errors`` keeps the server's validation model aligned with the
    browser's: the rejected pane opens and the exact control owns its message.

    Judgment relationships are deliberately absent. Test cases, validator state,
    and sample interactions are loaded only by their dedicated pages.
    """
    effective_validator_type = problem.validator_type if problem is not None else validator_type
    if effective_validator_type is None:
        raise ValueError("A problem form needs a validation strategy: pass validator_type when creating.")
    view = build_problem_form_view(
        request,
        problem=problem,
        validator_type=effective_validator_type,
        back_url=back_url,
        active_tab=active_tab,
        reselect_uploads=reselect_uploads,
    )
    resolved_field_errors = field_errors or {}
    context: dict[str, Any] = {
        "mode": mode,
        "problem": problem,
        "view": view,
        "validator_type": effective_validator_type,
        "is_edit_allowed": True,
        "form": form,
        "selected_cats_data": cats_data,
        "back_url": back_url,
        "return_state": state,
        "current_user": current_user,
        "is_admin": is_admin(current_user),
        "statement_languages": list(StatementLanguage),
        "editorial_release_policies": list(ArenaEditorialReleasePolicy),
        "expected_difficulty_levels": list(ArenaExpectedDifficulty),
        "expected_difficulty_stored": _off_anchor_difficulty(problem),
        "min_attempts_for_display": MIN_ATTEMPTS_FOR_DISPLAY,
        "language_conflict": language_conflict,
        "save_action": save_action,
        "errors": errors,
        "field_errors": resolved_field_errors,
        "first_error_field": next(iter(resolved_field_errors), ""),
        "detect_language_url": str(request.url_for("arena_admin_problem_detect_language")),
    }
    context["next_url"] = next_url or ""
    if mode == "edit" and problem is not None:
        context["problem_owner"] = problem_owner
        context["rating_history_url"] = str(
            request.url_for("arena_admin_problem_rating_history", problem_id=problem.id)
        )
    templates = request.app.state.arena_templates
    return html_response(
        templates.TemplateResponse(request, "admin/problem_form.html", context, status_code=status_code)
    )


async def edit_form_extras(
    problem: ArenaProblem,
    current_user: ArenaUser,
    session: AsyncSession,
) -> tuple[list[Any], ArenaUser | None]:
    """Load categories and admin-only owner data for the definition editor."""
    all_categories = await admin_problem_service.search_categories(session, query="", limit=200)
    problem_owner = await session.get(ArenaUser, problem.owner_id) if is_admin(current_user) else None
    return all_categories, problem_owner
