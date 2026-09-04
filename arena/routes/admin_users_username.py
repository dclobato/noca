#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin action: rename a user's public handle.

Its own module because ``admin_users_actions.py`` is already past the source-size
bound this project holds, and because this action is the one admin write that
overrides a *user-protective* limit rather than a merely operational one.

That is also why it is password-confirmed. Its nearest sibling by mechanism,
``change-name``, is not -- but its nearest sibling by *consequence* is
``toggle-public-profile``, which is: both change what the public sees about
someone, and this one additionally bypasses the cooldown that keeps a shielded
user's old and new handles from being correlated across the ranking. An admin
path weaker than the user path it mirrors is a way around the protection.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_users import ArenaUser
from arena.routes.admin_user_route_support import (
    NavState,
    _choose_redirect,
    _get_target_or_404,
    confirm_admin_password,
)
from arena.routes.user_username_api import (
    USERNAME_CHECK_RATE_LIMITER,
    check_rate_limit_policy,
)
from arena.services import admin_user_service
from arena.services.username_service import (
    UsernameError,
    is_username_available,
    normalize_username,
    validate_username,
)
from shared.services.admin_audit import record_admin_action
from shared.services.request_rate_limit import enforce_key_rate_limit
from shared.services.security_events import record_request_security_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["arena-admin"])


@router.get("/users/{user_id}/username/available", name="arena_admin_user_username_available")
async def admin_user_username_available(
    request: Request,
    user_id: str,
    q: str = Query(default=""),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Report whether a candidate handle is free **for this target user**.

    Deliberately not the self-service probe. That one excludes the *caller's*
    id, which is right when you are renaming yourself and wrong here: an admin
    typing the handle the target already holds would be told it is taken. This
    excludes the target instead, so re-submitting an unchanged handle reads as
    available rather than as a conflict.

    Read-only and advisory, exactly like its self-service sibling: the answer can
    be stale by the time the form is submitted, which is why the rename still has
    to handle a conflict.

    Args:
        request: Incoming request, used for the per-admin rate limit.
        user_id: The Arena user being renamed.
        q: The candidate handle as typed.
        admin: The authenticated Arena administrator.
        session: Active async database session.

    Returns:
        JSONResponse: ``{"username", "available", "error"}``. A malformed or
        reserved handle is a ``200`` with ``available: false`` and a message, so
        the field renders one feedback style whatever is wrong with the input.

    Raises:
        HTTPException: ``429`` with ``Retry-After`` when the acting admin exceeds
            the shared username-check window.
    """
    await enforce_key_rate_limit(
        request,
        policy=check_rate_limit_policy(),
        fallback_limiter=USERNAME_CHECK_RATE_LIMITER,
        key=admin.id,
        detail="Too many username checks. Please wait a moment.",
    )
    target = await _get_target_or_404(user_id, session)
    try:
        username = validate_username(q)
    except UsernameError as exc:
        return JSONResponse({"username": normalize_username(q), "available": False, "error": str(exc)})
    available = await is_username_available(session, username, exclude_user_id=target.id)
    return JSONResponse(
        {
            "username": username,
            "available": available,
            "error": None if available else "That username is already taken.",
        }
    )


@router.post("/users/{user_id}/change-username", name="arena_admin_user_change_username")
async def admin_user_change_username(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    new_username: str = Form(""),
    confirm_password: str = Form(...),
    allow_immediate_change: bool = Form(False),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Rename an Arena user's public handle, bypassing the change cooldown.

    Writes two records in the same transaction as the rename: an
    ``admin_action`` audit row and a ``username_changed`` security event, both
    naming the old and the new handle, and both recording whether the user was
    left free to rename again at once.

    Args:
        request: Incoming request.
        user_id: Target Arena user id.
        flash: Flash message dependency.
        nav: Hidden list-navigation state, so the redirect restores context.
        new_username: The handle as submitted by the administrator.
        confirm_password: The acting admin's own password.
        allow_immediate_change: Checkbox; when set, the target's cooldown is
            cleared instead of restarted. Defaults to False, so the protective
            behaviour is what happens when the admin says nothing.
        admin: The authenticated Arena administrator.
        session: Active async database session.

    Returns:
        Response: A 303 redirect back to the profile or the user list.
    """
    blocked = await confirm_admin_password(
        request,
        session,
        flash,
        admin=admin,
        password=confirm_password,
        failure_response=_choose_redirect(request, user_id, nav),
    )
    if blocked is not None:
        return blocked
    target = await _get_target_or_404(user_id, session)
    try:
        previous = await admin_user_service.admin_change_username(
            target,
            new_username,
            session,
            allow_immediate_change=allow_immediate_change,
        )
    except UsernameError as exc:
        await session.rollback()
        flash(str(exc), FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    if target.username == previous:
        flash("That is already this user's username.", FlashCategory.WARNING)
        return _choose_redirect(request, user_id, nav)
    try:
        await record_admin_action(
            session,
            request,
            module="arena",
            actor_user_id=admin.id,
            actor_label=admin.email_normalizado,
            action="change_username",
            target_type="arena_user",
            target_id=target.id,
            detail=(
                f"username: {previous} -> {target.username}; "
                f"cooldown={'cleared' if allow_immediate_change else 'restarted'}"
            ),
            severity="warning",
        )
        await record_request_security_event(
            session,
            request,
            module="arena",
            event_type="username_changed",
            actor_user_id=admin.id,
            actor_label=admin.email_normalizado,
            metadata={
                "source": "admin",
                "old": previous,
                "new": target.username,
                "user_id": target.id,
                "cooldown": "cleared" if allow_immediate_change else "restarted",
            },
        )
        await session.commit()
    except IntegrityError:
        # The availability pre-check inside change_username is a TOCTOU; the
        # UNIQUE constraint is the real guarantee. Losing that race is a
        # conflict to report, never a 500.
        await session.rollback()
        logger.info("Username %r was claimed before the admin rename committed", new_username)
        flash("That username is already taken.", FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)
    if allow_immediate_change:
        flash(f"Username changed to {target.username}. The user can change it again right away.", FlashCategory.SUCCESS)
    else:
        flash(f"Username changed to {target.username}.", FlashCategory.SUCCESS)
    return _choose_redirect(request, user_id, nav)
