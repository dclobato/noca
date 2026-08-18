#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena validation-strategy chooser, the entry point to problem creation.

``GET /admin/problems/new`` no longer renders a creation form. It offers the one
immutable decision a problem author makes -- which validation strategy the problem
uses -- and hands off to the strategy-specific creation editor at
``/admin/problems/new/{validator_type}``, whose route parameter is the sole
authority on the stored strategy.

The chooser sits between the problem list and the creation form, so it also has to
carry the list's return state (filters, sort, page, and an optional ``next``)
across itself; losing it here would drop the author back on an unfiltered list
after saving.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep

from arena.dependencies.admin import require_arena_problem_editor
from arena.models.arena_users import ArenaUser
from arena.routes.admin_problem_form_views import (
    html_response,
    problem_list_url,
    safe_next_path,
)
from arena.services import admin_problem_service
from shared.enumerations import ProblemValidatorType
from shared.services.validator_choice import (
    UnavailableValidatorChoiceError,
    UnknownValidatorChoiceError,
    resolve_validator_choice,
)

router = APIRouter(prefix="/admin", tags=["arena-admin"])


def creation_return_query(
    *,
    page: str,
    per_page: str,
    search: str,
    sort_by: str,
    owner_id: str,
    category_slugs: list[str] | None,
    language: str,
    enabled: str,
    editorial: str = "",
    next_path: str,
) -> str:
    """Build the query string that carries list-return state into a creation form.

    Only non-empty values are emitted, so a plain "Add new problem" click produces
    a clean URL rather than a string of empty parameters.

    Args:
        page: Requested list page.
        per_page: Requested page size.
        search: Active search term.
        sort_by: Active sort key.
        owner_id: Active owner filter.
        category_slugs: Active category filters.
        language: Active statement-language filter.
        enabled: Active enabled/disabled filter.
        editorial: Active editorial filter.
        next_path: Validated same-origin return path, or an empty string.

    Returns:
        str: A ``?``-prefixed query string, or an empty string when nothing is set.
    """
    params: list[tuple[str, str]] = []
    scalars = {
        "page": page,
        "per_page": per_page,
        "search": search,
        "sort_by": sort_by,
        "owner_id": owner_id,
        "language": language,
        "enabled": enabled,
        "editorial": editorial,
        "next": next_path,
    }
    params.extend((key, value) for key, value in scalars.items() if value)
    params.extend(("category_slugs", slug) for slug in (category_slugs or []) if slug)
    return f"?{urlencode(params)}" if params else ""


def resolve_choice_or_redirect(
    request: Request,
    flash: FlashDep,
    raw_validator_type: str,
    return_query: str = "",
) -> ProblemValidatorType | RedirectResponse:
    """Resolve a creation route's ``{validator_type}`` path segment.

    Args:
        request: The active request, used to build the chooser URL.
        flash: The flash dependency, used to explain a reserved strategy.
        raw_validator_type: The raw path segment.
        return_query: List-return query string to preserve on the redirect.

    Returns:
        ProblemValidatorType | RedirectResponse: The choosable strategy, or a
        ``303`` back to the chooser when the segment names the reserved
        ``checker`` strategy -- so typing the URL cannot bypass its disabled card.

    Raises:
        HTTPException: ``404`` when the segment names no strategy at all.
    """
    try:
        return resolve_validator_choice(raw_validator_type)
    except UnavailableValidatorChoiceError as exc:
        flash(str(exc), FlashCategory.WARNING)
        chooser = str(request.url_for("arena_admin_problem_new_choose"))
        return RedirectResponse(url=f"{chooser}{return_query}", status_code=303)
    except UnknownValidatorChoiceError as exc:
        raise HTTPException(404, "Unknown validation strategy.") from exc


@router.get("/problems/new", response_class=HTMLResponse, name="arena_admin_problem_new_choose")
async def admin_problem_new_choose(
    request: Request,
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
    current_user: ArenaUser = Depends(require_arena_problem_editor),
) -> Response:
    """Render the validation-strategy chooser for a new Arena problem."""
    del flash
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

    def form_url(strategy: ProblemValidatorType) -> str:
        base = str(request.url_for("arena_admin_problem_new", validator_type=strategy.value))
        return f"{base}{return_query}"

    templates = request.app.state.arena_templates
    context: dict[str, Any] = {
        "current_user": current_user,
        "back_url": back_url,
        "standard_url": form_url(ProblemValidatorType.STANDARD),
        "interactive_url": form_url(ProblemValidatorType.INTERACTIVE),
        "import_url": str(request.url_for("arena_admin_problem_import_form")),
    }
    return html_response(templates.TemplateResponse(request, "admin/problem_new_choose.html", context))
