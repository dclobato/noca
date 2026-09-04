#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin action: lift every sign-in lockout of one user.

Sits beside the other per-user POST modules (``admin_users_google.py``,
``admin_users_username.py``). The account's buckets are derived from the
user row (:func:`arena.services.lockout_admin_service.hashes_for_user`), so
the operator never types an identifier; the IP buckets are deliberately not
touched here, because an address is shared by more than one person and is
lifted from the dashboard page instead.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi_flash import FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_users import ArenaUser
from arena.routes.admin_user_route_support import (
    NavState,
    _build_nav_params,
    _get_target_or_404,
    _redirect,
    confirm_admin_password,
)
from arena.services import lockout_admin_service
from shared.services.auth_lockout_flow import perform_audited_unlock

router = APIRouter(prefix="/admin", tags=["arena-admin"])

_SECURITY_TAB = "personal-security"


def _security_tab_redirect(request: Request, user_id: str, nav: NavState) -> RedirectResponse:
    """Back to the profile's security tab, or to the list, preserving filter state."""
    params = _build_nav_params(nav)
    if nav.source == "profile":
        params["tab"] = _SECURITY_TAB
        return _redirect(str(request.url_for("arena_admin_user_profile", user_id=user_id)), params)
    return _redirect(str(request.url_for("arena_admin_user_list")), params)


@router.post("/users/{user_id}/unlock", name="arena_admin_user_unlock")
async def admin_user_unlock(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    confirm_password: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Clear every Arena account lockout of one user.

    Password-confirmed like its siblings, and recorded as an ``admin_action``
    row (``action=unlock_account``, warning severity) in the same transaction.

    Args:
        request: Incoming request.
        user_id: Target Arena user id.
        flash: Flash message dependency.
        nav: Hidden list-navigation state, so the redirect restores context.
        confirm_password: The acting admin's own password.
        admin: The authenticated Arena administrator.
        session: Active async database session.

    Returns:
        Response: A 303 redirect back to the profile's security tab or the user list.
    """
    redirect = _security_tab_redirect(request, user_id, nav)
    blocked = await confirm_admin_password(
        request, session, flash, admin=admin, password=confirm_password, failure_response=redirect
    )
    if blocked is not None:
        return blocked
    target = await _get_target_or_404(user_id, session)
    await perform_audited_unlock(
        request,
        session,
        flash,
        module="arena",
        actor_user_id=admin.id,
        actor_label=admin.email_normalizado,
        subject=lockout_admin_service.subject_for_hashes(lockout_admin_service.hashes_for_user(target)),
        action="unlock_account",
        target_type="arena_user",
        target_id=target.id,
    )
    return redirect
