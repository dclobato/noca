#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin action: unlink a user's Google account.

Its own module because ``admin_users_actions.py`` is already past the source-size
bound this project holds, following the ``admin_users_username.py`` precedent.

Password-confirmed, like ``disable-2fa``: both strip a credential from someone
else's account. Unlike the self-service unlink it is **not** refused when Google
is the account's only way in. That guard exists so a user cannot lock themselves
out by accident; an administrator does this on purpose -- a lost, compromised, or
mis-attached Google account -- and the account keeps its recovery path, the
ordinary password reset, which sets a real password over the placeholder. The
route therefore says so, twice: a warning flash for the administrator and, in
the notification email, the reset link for the user.

That recovery claim is exactly what an **unfinished** Google-first signup lacks
(``google_signup_service.admin_unlink_refusal``): with the identity gone, a
never-completed row could be finished by neither door while still blocking the
address, a held 13-17 signup could no longer be resumed, and an under-13 refusal
would lose the row that enforces it. Those are refused outright, with the
reason, and the profile page shows that reason in place of the control.

It is also deliberately independent of ``NOCA_ARENA_GOOGLE_OAUTH_ENABLED``: the
identity row outlives the feature switch, and an operator who turned Google
sign-in off must still be able to detach the identities it created.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.database import get_db
from arena.dependencies.admin import require_arena_admin
from arena.models.arena_users import ArenaUser
from arena.routes.admin_user_route_support import (
    NavState,
    _choose_redirect,
    _get_target_or_404,
    confirm_admin_password,
)
from arena.services import (
    admin_user_service,
    google_identity_service,
    google_signup_service,
    user_security_notification_service,
)
from shared.services.admin_audit import record_admin_action
from shared.services.security_events import record_request_security_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["arena-admin"])


def _password_reset_url(request: Request) -> str:
    """Return the absolute password-reset URL for the notification email.

    Built from the configured public base rather than ``request.url_for`` so the
    link is right behind a reverse proxy, exactly as the password-reset flow
    builds its own links.
    """
    base = settings.ARENA_URL_BASE or str(request.base_url).rstrip("/")
    return f"{base}{request.app.url_path_for('arena_password_reset')}"


@router.post("/users/{user_id}/unlink-google", name="arena_admin_user_unlink_google")
async def admin_user_unlink_google(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    confirm_password: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Detach the Google account linked to an Arena user.

    Refused, changing nothing, for an unfinished Google-first signup (see the
    module docstring). Otherwise writes two records in the same transaction as
    the unlink: an
    ``admin_action`` audit row and a ``google_account_unlinked`` security event
    (the same event type the self-service unlink records, distinguished by
    ``source=admin``), both stating whether the account still has a usable
    password. The user is signed out and emailed; a delivery failure does not
    roll back the security action.

    Args:
        request: Incoming request.
        user_id: Target Arena user id.
        flash: Flash message dependency.
        nav: Hidden list-navigation state, so the redirect restores context.
        confirm_password: The acting admin's own password.
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
    identity = await google_identity_service.get_identity_for_user(session, target.id)
    if identity is None:
        flash("No Google account is linked to this user.", FlashCategory.WARNING)
        return _choose_redirect(request, user_id, nav)

    refusal = google_signup_service.admin_unlink_refusal(target)
    if refusal is not None:
        flash(f"Google cannot be unlinked from this account. {refusal}", FlashCategory.DANGER)
        return _choose_redirect(request, user_id, nav)

    password_usable = target.has_usable_password
    logger.warning(
        "Admin %s (%s) unlink_google on %s (%s); password_usable=%s",
        admin.id,
        admin.email_normalizado,
        target.id,
        target.email_normalizado,
        password_usable,
    )
    await admin_user_service.admin_unlink_google(target, identity, session)
    await record_admin_action(
        session,
        request,
        module="arena",
        actor_user_id=admin.id,
        actor_label=admin.email_normalizado,
        action="unlink_google",
        target_type="arena_user",
        target_id=target.id,
        detail=f"password_usable={password_usable}",
        severity="warning",
    )
    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type="google_account_unlinked",
        severity="warning",
        actor_user_id=admin.id,
        actor_label=admin.email_normalizado,
        metadata={"source": "admin", "user_id": target.id, "password_usable": password_usable},
    )
    await session.commit()

    try:
        # The reset-URL construction is deliberately inside the try: the unlink
        # is already committed, so a route-name lookup failure must degrade to
        # the "could not be sent" flash below rather than a 500 after commit.
        sent = await user_security_notification_service.send_admin_google_unlinked_email(
            target,
            request.app.state.email_service,
            admin_id=admin.id,
            password_reset_url=None if password_usable else _password_reset_url(request),
            account_inactive=not target.ativo,
        )
    except Exception:
        logger.exception("Failed to send Google-unlinked notification to %s", target.email_normalizado)
        sent = False

    flash("Google account unlinked. The user was signed out.", FlashCategory.SUCCESS)
    if not password_usable:
        note = (
            "This account has no password: the user must set one through “Forgot password?” "
            "before signing in with a password."
        )
        if not target.ativo:
            note += " The account is also deactivated, so a password alone will not let them in."
        flash(note, FlashCategory.WARNING)
    if not sent:
        flash("Google was unlinked, but the notification email could not be sent.", FlashCategory.WARNING)
    return _choose_redirect(request, user_id, nav)
