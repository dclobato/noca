#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin user management — list and profile GET routes.

POST action routes live in admin_users_actions.py.
Shared helpers (NavState, guards, redirect builder) live in admin_user_route_support.py.

GET routes provide paginated browsing and detailed inspection of Arena user
accounts from the admin panel.  Back-navigation query params forwarded from the
calling list view are preserved on the profile page as raw values (no role
normalization) so the breadcrumb link always returns the user to exactly the
list they came from.
"""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi_flash import FlashDep
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_users import ArenaUser
from arena.routes.admin_date_helpers import _effective_per_page, _local_midnight_to_utc, _parse_date_param
from arena.routes.admin_user_route_support import (
    _ALLOWED_PER_PAGE,
    _DEFAULT_PER_PAGE,
    _get_target_or_404,
    _html,
)
from arena.services import admin_login_history_service, admin_user_service
from arena.services.pagination_service import build_pagination_params, parse_page
from arena.services.user_timezone_service import timezone_name_for_user
from shared.db_schema.arena.arena_heatmap import arena_user_submission_heatmap
from shared.enumerations import ArenaRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["arena-admin"])

_CREDITS_PER_PAGE = 25
_LOGIN_HISTORY_ALLOWED_PER_PAGE: set[int] = {10, 25, 50, 100, 500}
_LOGIN_HISTORY_DEFAULT_PER_PAGE = 25


@router.get("/users", response_class=HTMLResponse, name="arena_admin_user_list")
async def admin_user_list(
    request: Request,
    flash: FlashDep,
    page: str | None = None,
    per_page: str | None = None,
    search: str | None = None,
    role: str | None = None,
    can_edit: str | None = None,
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the paginated Arena admin user list with optional search and role filtering."""
    try:
        effective_per_page = int(per_page) if per_page else _DEFAULT_PER_PAGE
    except TypeError, ValueError:
        effective_per_page = _DEFAULT_PER_PAGE
    if effective_per_page not in _ALLOWED_PER_PAGE:
        effective_per_page = _DEFAULT_PER_PAGE

    try:
        role_filter: ArenaRole | None = ArenaRole(role) if role else None
    except ValueError:
        role_filter = None

    can_edit_only = can_edit == "1"

    pagination = await admin_user_service.list_users_paginated(
        session,
        page=parse_page(page),
        per_page=effective_per_page,
        search=search or "",
        role_filter=role_filter,
        can_edit_only=can_edit_only,
    )

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/user_list.html",
            {
                "pagination": pagination,
                "search": search or "",
                "per_page": effective_per_page,
                "role_filter": role_filter,
                "can_edit_filter": can_edit_only,
                "ArenaRole": ArenaRole,
                "current_user": admin,
            },
        )
    )


@router.get("/users/{user_id}", response_class=HTMLResponse, name="arena_admin_user_profile")
async def admin_user_profile(
    request: Request,
    user_id: str,
    flash: FlashDep,
    tab: str | None = None,
    credits_page: str | None = None,
    login_page: str | None = None,
    login_per_page: str | None = None,
    login_sort_dir: str = "desc",
    login_date_from: str | None = None,
    login_date_to: str | None = None,
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the admin view of an Arena user's profile, or raise 404 if not found.

    Shows personal/security details, AI credits, and login history.
    Back-navigation query params are forwarded from the calling list view.
    """
    target = await _get_target_or_404(user_id, session)

    if credits_page is not None:
        active_tab = "credits"
    elif any(value is not None for value in (login_page, login_per_page, login_date_from, login_date_to)):
        active_tab = "login-history"
    else:
        active_tab = tab if tab in {"personal-security", "credits", "login-history"} else "personal-security"

    credit_transactions = None
    if active_tab == "credits":
        credit_transactions = await admin_user_service.get_credit_transactions_paginated(
            session,
            user_id=target.id,
            params=build_pagination_params(credits_page, per_page=_CREDITS_PER_PAGE),
        )

    login_history = None
    resolved_login_per_page = _effective_per_page(
        login_per_page, allowed=_LOGIN_HISTORY_ALLOWED_PER_PAGE, default=_LOGIN_HISTORY_DEFAULT_PER_PAGE
    )
    resolved_login_sort_dir = "asc" if login_sort_dir == "asc" else "desc"
    if active_tab == "login-history":
        parsed_from = _parse_date_param(login_date_from)
        parsed_to = _parse_date_param(login_date_to)
        timezone_name = timezone_name_for_user(admin)
        date_from_utc = _local_midnight_to_utc(parsed_from, timezone_name) if parsed_from else None
        date_to_utc = _local_midnight_to_utc(parsed_to + timedelta(days=1), timezone_name) if parsed_to else None
        login_history = await admin_login_history_service.list_login_history_paginated(
            session,
            user_id=target.id,
            page=parse_page(login_page),
            per_page=resolved_login_per_page,
            sort_dir=resolved_login_sort_dir,
            date_from_utc=date_from_utc,
            date_to_utc=date_to_utc,
        )

    # Preserve raw back-navigation params without role normalization so the
    # breadcrumb link returns to exactly the list the admin came from.
    back_search = request.query_params.get("search", "")
    back_page = request.query_params.get("page", "1")
    back_per_page = request.query_params.get("per_page", str(_DEFAULT_PER_PAGE))
    back_role = request.query_params.get("role", "")

    back_params: dict[str, str] = {}
    if back_search:
        back_params["search"] = back_search
    if back_page and back_page != "1":
        back_params["page"] = back_page
    try:
        eff_per_page = int(back_per_page)
    except ValueError:
        eff_per_page = _DEFAULT_PER_PAGE
    if eff_per_page != _DEFAULT_PER_PAGE:
        back_params["per_page"] = str(eff_per_page)
    if back_role:
        back_params["role"] = back_role

    back_base = str(request.url_for("arena_admin_user_list"))
    back_url = f"{back_base}?{'&'.join(f'{k}={v}' for k, v in back_params.items())}" if back_params else back_base

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "admin/user_profile.html",
            {
                "target_user": target,
                "credit_transactions": credit_transactions,
                "login_history": login_history,
                "login_per_page": resolved_login_per_page,
                "login_sort_dir": resolved_login_sort_dir,
                "login_date_from": login_date_from or "",
                "login_date_to": login_date_to or "",
                "active_tab": active_tab,
                "back_url": back_url,
                "back_search": back_search,
                "back_page": back_page,
                "back_per_page": back_per_page,
                "back_role": back_role,
                "ArenaRole": ArenaRole,
                "current_user": admin,
            },
        )
    )


@router.get(
    "/users/{user_id}/submission-heatmap",
    name="arena_admin_user_submission_heatmap",
)
async def admin_user_submission_heatmap(
    user_id: str,
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Return the precomputed submission heatmap for a given Arena user."""
    target = await _get_target_or_404(user_id, session)
    row = (
        (
            await session.execute(
                select(
                    arena_user_submission_heatmap.c.data,
                    arena_user_submission_heatmap.c.range_start,
                    arena_user_submission_heatmap.c.range_end,
                    arena_user_submission_heatmap.c.computed_at,
                ).where(arena_user_submission_heatmap.c.user_id == target.id)
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        today = datetime.now(tz=UTC).date()
        start = (today - timedelta(days=363)).isoformat()
        end = today.isoformat()
        return JSONResponse({"heatmap": [], "range_start": start, "range_end": end, "computed_at": None})
    return JSONResponse(
        {
            "heatmap": row["data"],
            "range_start": row["range_start"],
            "range_end": row["range_end"],
            "computed_at": row["computed_at"].isoformat() if row["computed_at"] else None,
        }
    )
