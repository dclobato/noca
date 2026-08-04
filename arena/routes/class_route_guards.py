#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared authorization guards and navigation URLs for class-scoped routes.

Every class-scoped problem-set page answers the same three questions before it
does anything: is someone logged in, may they manage Arena classes at all, and
is this particular class theirs. Each route module used to carry its own copy of
that logic, which is exactly the kind of duplication where one copy silently
drifts from the others. This module owns the single answer; route modules import
it rather than restating it.

The return-navigation builders live here for the same reason: the list and
report URLs carry pagination and sort context that several pages hand back and
forth, and a page that forgets a parameter drops the user somewhere they did not
come from.
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_users import ArenaUser
from arena.services import arena_class_detail_service
from arena.services.arena_class_service import ArenaClassNotFoundError
from arena.services.session_service import build_current_next_url, build_login_redirect_response
from shared.enumerations import ArenaRole

#: Roles allowed to reach any class-management page at all. Owning the specific
#: class is a separate check performed by :func:`require_problem_set_manager`.
MANAGER_ROLES = frozenset({ArenaRole.ARENA_ADMIN, ArenaRole.ARENA_JUDGE})


def html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def require_user(request: Request, current_user: ArenaUser | None) -> ArenaUser | RedirectResponse:
    """Return the current user, or a login redirect that returns here afterwards."""
    if current_user is None:
        return build_login_redirect_response(request, next_url=build_current_next_url(request))
    return current_user


def is_manager(user: ArenaUser) -> bool:
    """Return whether the user may manage Arena classes."""
    return user.role in MANAGER_ROLES


def _with_list_context(
    url: str,
    *,
    page: int | str | None,
    sort: str | None,
    direction: str | None,
) -> str:
    """Append the non-empty pagination/sort parameters to a URL."""
    params = {
        key: str(value)
        for key, value in {"page": page, "sort": sort, "direction": direction}.items()
        if value not in {None, ""}
    }
    return f"{url}?{urlencode(params)}" if params else url


def problem_set_list_url(
    request: Request,
    *,
    class_id: str,
    page: int | str | None = None,
    sort: str | None = None,
    direction: str | None = None,
) -> str:
    """Build a problem-set list URL carrying the caller's list context."""
    return _with_list_context(
        str(request.url_for("arena_class_problem_set_list", class_id=class_id)),
        page=page,
        sort=sort,
        direction=direction,
    )


def problem_set_report_url(
    request: Request,
    *,
    class_id: str,
    set_id: str,
    page: int | str | None = None,
    sort: str | None = None,
    direction: str | None = None,
) -> str:
    """Build a per-set report URL carrying the caller's list context."""
    return _with_list_context(
        str(request.url_for("arena_class_problem_set_report", class_id=class_id, set_id=set_id)),
        page=page,
        sort=sort,
        direction=direction,
    )


async def require_problem_set_manager(
    request: Request,
    current_user: ArenaUser | None,
    *,
    class_id: str,
    session: AsyncSession,
) -> tuple[ArenaUser | RedirectResponse, Any | None]:
    """Return the logged-in teacher/admin and the class detail for a class page.

    Args:
        request: Current HTTP request.
        current_user: Authenticated Arena user, or ``None`` for guests.
        class_id: UUID of the ``arena_classes`` row being managed.
        session: Active database session.

    Returns:
        tuple: The user and the loaded class detail. When nobody is logged in,
        the first element is a login redirect and the second is ``None``; the
        caller must return that redirect before using the detail.

    Raises:
        HTTPException: 403 when the caller cannot manage classes or does not own
            this one, 404 when the class does not exist.
    """
    user_or_redirect = require_user(request, current_user)
    if isinstance(user_or_redirect, RedirectResponse):
        return user_or_redirect, None
    if not is_manager(user_or_redirect):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        detail = await arena_class_detail_service.get_class_detail(session, class_id=class_id, today=date.today())
    except ArenaClassNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Class not found") from exc
    if user_or_redirect.role != ArenaRole.ARENA_ADMIN and detail.teacher_id != user_or_redirect.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return user_or_redirect, detail
