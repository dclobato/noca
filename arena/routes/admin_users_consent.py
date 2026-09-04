#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena admin parental-consent action route.

Split out of ``admin_users_actions.py``, which is already past the ``AGENTS.md`` size
bound, following the decomposition ``admin_users_username.py`` set.

The toggle is a privileged action on a legal control, so it carries the same three
guarantees its siblings do and this route's predecessor did not: the admin re-confirms
their password, the action is written to the admin audit log, and the security-event log
records the consent transition. Its *effect* comes from the same ``user_service`` write
paths the guardian's own link uses, so an admin revocation cannot be weaker than a
guardian revocation.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from fastapi_flash import FlashCategory, FlashDep
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
from arena.services import admin_user_service
from shared.services.admin_audit import record_admin_action
from shared.services.security_events import record_request_security_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["arena-admin"])


@router.post("/users/{user_id}/toggle-parental-consent", name="arena_admin_user_toggle_parental_consent")
async def admin_user_toggle_parental_consent(
    request: Request,
    user_id: str,
    flash: FlashDep,
    nav: Annotated[NavState, Depends()],
    confirm_password: str = Form(...),
    admin: ArenaUser = Depends(require_arena_admin),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Grant or withdraw parental consent for an Arena user.

    Withdrawing suspends the account and kills its live sessions; granting restores it
    when the remaining gates are clear. Either way the consent epoch advances, so a
    revocation link sitting in a guardian's mailbox stops working.

    Args:
        request: Incoming request.
        user_id: Target Arena user id.
        flash: Flash message dependency.
        nav: Hidden list-navigation state, so the redirect restores the admin's context.
        confirm_password: The acting admin's password, re-confirmed for this action.
        admin: The authenticated Arena admin.
        session: Active async database session.

    Returns:
        Response: Redirect back to the admin's previous context.
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
    logger.warning(
        "Admin %s (%s) toggle_parental_consent on %s (%s)",
        admin.id,
        admin.email_normalizado,
        target.id,
        target.email_normalizado,
    )
    outcome = await admin_user_service.admin_toggle_parental_consent(target, session)
    await record_admin_action(
        session,
        request,
        module="arena",
        actor_user_id=admin.id,
        actor_label=admin.email_normalizado,
        action="parental_consent_granted" if outcome.granted else "parental_consent_revoked",
        target_type="arena_user",
        target_id=target.id,
        detail=f"consentimento_responsavel={target.consentimento_responsavel}",
        severity="warning",
    )
    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type="parental_consent_granted" if outcome.granted else "parental_consent_revoked",
        severity="warning",
        actor_user_id=target.id,
        actor_label=admin.email_normalizado,
        metadata={"source": "admin"},
    )
    if outcome.activated:
        await record_request_security_event(
            session,
            request,
            module="arena",
            event_type="account_activated",
            actor_user_id=target.id,
            actor_label=admin.email_normalizado,
            metadata={"source": "parental_consent"},
        )
    if not outcome.granted:
        await record_request_security_event(
            session,
            request,
            module="arena",
            event_type="account_deactivated",
            severity="warning",
            actor_user_id=target.id,
            actor_label=admin.email_normalizado,
            metadata={"source": "parental_consent_revocation"},
        )
    await session.commit()
    flash(
        "Parental consent granted." if outcome.granted else "Parental consent revoked and the account suspended.",
        FlashCategory.SUCCESS,
    )
    return _choose_redirect(request, user_id, nav)
