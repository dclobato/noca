#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared helpers for Arena admin-user route modules.

NavState consolidates the five hidden list-navigation fields that every action
form posts back so redirects can restore the user's previous filter state.
The other helpers (guards, redirect builder, 404 fetcher) are used by both
admin_users.py (GET routes) and admin_users_actions.py (POST routes).
"""

from typing import Any, cast

from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from arena.models.arena_users import ArenaUser
from arena.services import admin_user_service
from shared.enumerations import ArenaRole

_ALLOWED_PER_PAGE: list[int] = [10, 25, 50, 100, 500]
_DEFAULT_PER_PAGE: int = 25


class NavState:
    """Captures the list-navigation state carried by every action form's hidden fields."""

    def __init__(
        self,
        source: str = Form(""),
        search: str = Form(""),
        page: str = Form("1"),
        per_page: str = Form("25"),
        role_filter: str = Form(""),
    ) -> None:
        """Initialise from POST form fields."""
        self.source = source
        self.search = search
        self.page = page
        self.per_page = per_page
        self.role_filter = role_filter


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


async def _get_target_or_404(user_id: str, session: AsyncSession) -> ArenaUser:
    """Fetch an Arena user by id, raising HTTP 404 if not found."""
    result = await session.execute(
        select(ArenaUser).options(selectinload(ArenaUser.affiliation)).where(ArenaUser.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def _check_self_guard(admin: ArenaUser, target: ArenaUser, action: str, flash: FlashDep) -> bool:
    """Return True and flash a warning if the admin is acting on their own account."""
    if admin.id == target.id:
        flash(f"You cannot {action} your own account.", FlashCategory.WARNING)
        return True
    return False


async def _check_last_admin_guard(target: ArenaUser, session: AsyncSession, flash: FlashDep) -> bool:
    """Return True and flash a warning if the action would remove the last Arena admin."""
    if target.role == ArenaRole.ARENA_ADMIN and await admin_user_service.count_admins(session) <= 1:
        flash(
            "Cannot perform this action — there must always be at least one Arena Admin.",
            FlashCategory.WARNING,
        )
        return True
    return False


def _parse_role_filter(value: str) -> ArenaRole | None:
    """Parse a role filter string into an ArenaRole or None."""
    if not value:
        return None
    try:
        return ArenaRole(value)
    except ValueError:
        return None


def _redirect(url: str, params: dict[str, str]) -> RedirectResponse:
    """Build a 303 RedirectResponse, appending ``params`` as a query string when non-empty."""
    if params:
        return RedirectResponse(url=f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}", status_code=303)
    return RedirectResponse(url=url, status_code=303)


def _build_nav_params(nav: NavState) -> dict[str, str]:
    """Build query-string params for action redirects, normalizing role_filter."""
    try:
        eff = int(nav.per_page)
    except TypeError, ValueError:
        eff = _DEFAULT_PER_PAGE
    params: dict[str, str] = {}
    if nav.search:
        params["search"] = nav.search
    if nav.page and nav.page != "1":
        params["page"] = nav.page
    if eff != _DEFAULT_PER_PAGE:
        params["per_page"] = str(eff)
    parsed = _parse_role_filter(nav.role_filter)
    if parsed is not None:
        params["role"] = parsed.value
    return params


def _choose_redirect(request: Request, user_id: str, nav: NavState) -> RedirectResponse:
    """Return a 303 redirect to the user profile or the user list, preserving filter state.

    ``nav.source == "profile"`` redirects to the user profile; any other value goes to the list.
    """
    params = _build_nav_params(nav)
    if nav.source == "profile":
        return _redirect(str(request.url_for("arena_admin_user_profile", user_id=user_id)), params)
    return _redirect(str(request.url_for("arena_admin_user_list")), params)
