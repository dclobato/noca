#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Contest-admin validation-strategy chooser, the entry point to problem creation.

``GET /c/{slug}/admin/problems/new`` no longer renders a creation form. It offers
the one immutable decision a problem author makes -- which validation strategy the
problem uses -- and then hands off to the strategy-specific creation editor at
``/new/{validator_type}``, whose route parameter is the sole authority on the
stored strategy.

This lives in its own router rather than in ``contest_admin_problem.py`` so the
chooser does not enlarge an already oversized module; several routers already
share the ``/c/{slug}/admin/problems`` prefix.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_flash import FlashCategory, FlashDep

from shared.enumerations import ProblemValidatorType
from shared.services.validator_choice import (
    UnavailableValidatorChoiceError,
    UnknownValidatorChoiceError,
    resolve_validator_choice,
)
from web.dependencies import ContestAdminContext, get_contest_admin_context
from web.routes.contest_admin_problem_helpers import _html, _is_edit_allowed, _redirect

router = APIRouter(prefix="/c/{slug}/admin/problems", tags=["contest_admin_problems"])


def resolve_choice_or_redirect(
    request: Request,
    flash: FlashDep,
    slug: str,
    raw_validator_type: str,
) -> ProblemValidatorType | RedirectResponse:
    """Resolve a creation route's ``{validator_type}`` path segment.

    Args:
        request: The active request, used to build the chooser URL.
        flash: The flash dependency, used to explain a reserved strategy.
        slug: The contest login slug.
        raw_validator_type: The raw path segment.

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
        return _redirect(str(request.url_for("new_problem_choose", slug=slug)))
    except UnknownValidatorChoiceError as exc:
        raise HTTPException(404, "Unknown validation strategy.") from exc


@router.get("/new", response_class=HTMLResponse, name="new_problem_choose")
async def new_problem_choose(
    request: Request,
    ctx: ContestAdminContext = Depends(get_contest_admin_context),
) -> HTMLResponse:
    """Render the validation-strategy chooser for a new contest problem."""
    templates = request.app.state.templates
    slug = ctx.contest.login_slug
    return _html(
        templates.TemplateResponse(
            request,
            "admin/problems/new_choose.html",
            {
                "current_user": ctx.actor,
                "contest": ctx.contest,
                "is_edit_allowed": _is_edit_allowed(ctx.contest),
                "standard_url": str(
                    request.url_for(
                        "new_problem_form",
                        slug=slug,
                        validator_type=ProblemValidatorType.STANDARD.value,
                    )
                ),
                "interactive_url": str(
                    request.url_for(
                        "new_problem_form",
                        slug=slug,
                        validator_type=ProblemValidatorType.INTERACTIVE.value,
                    )
                ),
                "import_url": str(request.url_for("import_problem_form", slug=slug)),
            },
        )
    )


__all__ = ["router", "new_problem_choose", "resolve_choice_or_redirect"]
