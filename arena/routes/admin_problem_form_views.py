#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
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
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.models.arena_problems import ArenaProblem
from arena.models.arena_submissions import ArenaSubmission
from arena.models.arena_users import ArenaUser
from arena.services import admin_problem_service, admin_problem_tc_service
from arena.services.admin_problem_tc_service import TestCaseView
from shared.enumerations import ArenaRole
from shared.services.imageprocessing_service import ImageProcessingService
from shared.services.testcase_view import TestCaseRowView

MAX_IMAGE_SIZE = 2 * 1024 * 1024  # 2 MB — limit for problem illustration images

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
    resolves module-specific ``url_for`` names.
    """
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
                edit_url=str(request.url_for("arena_admin_problem_tc_edit", problem_id=problem_id, tc_id=tc.id)),
                download_url=str(
                    request.url_for("arena_admin_problem_tc_download", problem_id=problem_id, tc_id=tc.id)
                ),
                replace_url=str(request.url_for("arena_admin_problem_tc_replace", problem_id=problem_id, tc_id=tc.id)),
                move_url=str(request.url_for("arena_admin_problem_tc_move", problem_id=problem_id, tc_id=tc.id)),
                toggle_sample_url=str(
                    request.url_for("arena_admin_problem_tc_toggle_sample", problem_id=problem_id, tc_id=tc.id)
                ),
            )
        )
    return rows


def effective_per_page(value: str | None) -> int:
    """Return an allowed page size, falling back to the default."""
    try:
        effective = int(value) if value else DEFAULT_PER_PAGE
    except TypeError, ValueError:
        return DEFAULT_PER_PAGE
    return effective if effective in ALLOWED_PER_PAGE else DEFAULT_PER_PAGE


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
    sort_by: str = "title_asc",
    owner_id: str = "",
    category_slugs: list[str] | None = None,
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
    if sort_by and sort_by != "title_asc":
        params["sort_by"] = sort_by
    if owner_id:
        params["owner_id"] = owner_id
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
    result = await image_service.process_upload_image(image, max_file_size=MAX_IMAGE_SIZE)
    return result.imagem_base64, result.mime_type


def form_fields(
    *,
    title: str,
    author: str,
    author_is_owner: bool,
    source: str,
    hide_author_show_source: bool,
    time_limit_ms: int,
    memory_limit_kb: int,
    pids_limit: int,
    output_limit_in_bytes: int,
    problem_statement: str,
    category_ids: list[str],
    image_caption: str,
    notes: str = "",
    license: str = "",
) -> dict[str, Any]:
    """Build the ``form`` context dict consumed by ``problem_form.html``."""
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
        "category_ids": category_ids,
        "image_caption": image_caption,
        "notes": notes,
        "license": license,
    }


def return_state(
    *,
    page: str,
    per_page: str,
    search: str,
    sort_by: str,
    owner_id: str,
    category_slugs: list[str] | None,
) -> dict[str, Any]:
    """Build the hidden return-state dict that preserves list filters across the form."""
    return {
        "page": page,
        "per_page": per_page,
        "search": search,
        "sort_by": sort_by,
        "owner_id": owner_id,
        "category_slugs": category_slugs or [],
    }


def render_problem_form(
    request: Request,
    *,
    mode: str,
    form: dict[str, Any],
    cats_data: list[dict[str, str]],
    back_url: str,
    state: dict[str, Any],
    current_user: ArenaUser,
    problem: ArenaProblem | None = None,
    test_cases: list[Any] | None = None,
    next_url: str | None = None,
    problem_owner: ArenaUser | None = None,
    has_submissions: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    """Render ``problem_form.html`` with the shared create/edit context.

    Edit-only context keys (``next_url``, ``problem_owner``, ``rating_history_url``)
    are added only when ``mode == "edit"``.
    """
    rows = (
        build_testcase_row_views(request, problem.id, cast(list[TestCaseView], test_cases))
        if mode == "edit" and problem is not None
        else []
    )
    context: dict[str, Any] = {
        "mode": mode,
        "problem": problem,
        "test_cases": test_cases or [],
        "rows": rows,
        "is_edit_allowed": True,
        "form": form,
        "selected_cats_data": cats_data,
        "back_url": back_url,
        "return_state": state,
        "current_user": current_user,
        "is_admin": is_admin(current_user),
        "has_submissions": has_submissions,
    }
    if mode == "edit" and problem is not None:
        context["next_url"] = next_url or ""
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
) -> tuple[list[Any], list[Any], ArenaUser | None, bool]:
    """Load test cases, categories, admin-only owner data, and submission state."""
    test_cases = await admin_problem_tc_service.list_testcase_views(session, problem.id, settings.PROBLEM_TESTCASE_DIR)
    all_categories = await admin_problem_service.search_categories(session, query="", limit=200)
    problem_owner = await session.get(ArenaUser, problem.owner_id) if is_admin(current_user) else None
    result = await session.execute(select(exists().where(ArenaSubmission.problem_id == problem.id)))
    has_submissions: bool = result.scalar_one()
    return test_cases, all_categories, problem_owner, has_submissions
