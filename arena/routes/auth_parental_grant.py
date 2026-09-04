#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Guardian parental-consent grant routes (LGPD art. 8 §1).

The grant flow's two halves, split out of ``auth_signup`` (already past the
``AGENTS.md`` size bound) the same way the revocation flow lives in
``auth_parental_revoke``. The ``GET`` keeps the path and route name every consent email
already links to, but only *renders* a review page: mail scanners and link prefetchers
follow links in email, so the half that grants must be a ``POST`` the guardian submits.

The ``GET`` performs **no state mutation at all** -- not even throttle accounting. A
refused link answers the same flash-and-redirect the old flow used, but records no
``auth_failure`` row and moves no counter; the whole ``token_redeem`` contract (lockout
check, failure counting, reset on success) now lives on the ``POST``, the verb that can
act. The oracle cost is the one the revocation ``GET`` already accepted: the token is a
256-bit HMAC, so an unthrottled validity check helps nobody who does not already hold
the link.

Both halves resolve the token through
:func:`arena.services.user_email_service.resolver_consentimento_por_token`; the ``POST``
resolves it again under a row lock before granting, so it never trusts a validation the
``GET`` performed.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.routes.auth_common import _token_failure_message, send_consent_confirmation_email
from arena.routes.auth_token_redeem import (
    build_token_redeem_identity,
    reject_token_redemption,
    reset_token_redeem_throttle,
)
from arena.services import user_email_service, user_service
from arena.services.consent_timeline import build_consent_timeline
from shared.services.security_events import record_request_security_event

router = APIRouter(prefix="/auth", tags=["arena-auth"])
logger = logging.getLogger(__name__)

_TEMPLATE = "auth/parental_consent_grant.html"


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _redirect_to(request: Request, endpoint: str) -> RedirectResponse:
    """Build a 303 redirect to a named route endpoint."""
    return RedirectResponse(url=str(request.url_for(endpoint)), status_code=303)


@router.get("/parental-consent", response_class=HTMLResponse, name="arena_parental_consent")
async def arena_parental_consent(
    request: Request,
    flash: FlashDep,
    token: str = "",
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the consent review page for a guardian's link.

    Mutates nothing -- no consent change, no events, no mail, and no throttle
    accounting (see the module docstring for why a refused ``GET`` stays write-free).
    The page names the child's name and full email address, alongside the chronology
    of the consent, so the guardian can be certain which account they are authorizing.
    An already-consented account still renders the page, with its own node on that
    chronology; re-submitting is harmless by design.

    Args:
        request: Incoming request.
        flash: Flash-message dependency.
        token: Signed consent token from the email link.
        session: Active async database session.

    Returns:
        Response: The review page, or a flash-and-redirect for a refused link.
    """
    if not token:
        flash("Consent link is missing a token.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    result = await user_email_service.resolver_consentimento_por_token(token, session, request.app.state.jwt_service)
    if result.status != user_service.UserOperationStatus.SUCCESS or result.user is None:
        flash(_token_failure_message(result.status), FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            _TEMPLATE,
            {
                "token": token,
                "child_name": result.user.nome,
                "child_email": result.user.email_normalizado,
                "already_consented": bool(result.user.consentimento_responsavel),
                "timeline": build_consent_timeline(result.user),
            },
        )
    )


@router.post("/parental-consent", name="arena_parental_consent_submit")
async def arena_parental_consent_submit(
    request: Request,
    flash: FlashDep,
    token: str = Form(""),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Record parent/legal guardian consent from the review page's form.

    The mutating half, carrying everything the old ``GET`` did: the token is resolved
    **again** under a row lock and the grant applied inside it, failures count on the
    shared ``token_redeem`` bucket (a genuine link still redeems while the bucket is
    locked -- the lock refuses bad tokens only), and every side effect beyond
    activation is gated on the consent state having actually *moved*: a re-submitted
    form records no event and sends no mail. The confirmation email is sent after the
    commit, because it carries the guardian's revocation link and that link must hold
    the post-grant consent epoch.

    Args:
        request: Incoming request.
        flash: Flash-message dependency.
        token: Signed consent token from the review form.
        session: Active async database session.

    Returns:
        Response: A ``303`` to the login page, or the throttle lockout page.
    """
    if not token:
        flash("Consent link is missing a token.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    identity = build_token_redeem_identity(request, token)
    jwt_service = request.app.state.jwt_service
    result = await user_email_service.validar_consentimento_responsavel_por_token(token, session, jwt_service)
    if result.status != user_service.UserOperationStatus.SUCCESS or result.user is None:
        return await reject_token_redemption(request, session, flash, identity, result.status)

    transitioned = bool((result.extra_data or {}).get("transitioned"))
    was_active = result.user.ativo
    activated = await user_service.ativar_conta_se_pronta(result.user, session) and not was_active
    if transitioned:
        for event_type in ("parental_consent_confirmed", "parental_consent_granted"):
            await record_request_security_event(
                session,
                request,
                module="arena",
                event_type=event_type,
                actor_user_id=result.user.id,
                actor_label=result.user.email_responsavel_legal,
                metadata={"source": "parental_consent_token"},
            )
    if activated:
        await record_request_security_event(
            session,
            request,
            module="arena",
            event_type="account_activated",
            actor_user_id=result.user.id,
            actor_label=result.user.email_normalizado,
            metadata={"source": "parental_consent"},
        )
    await session.commit()
    if transitioned:
        await send_consent_confirmation_email(request, session, result.user)
    await reset_token_redeem_throttle(request, identity)
    if activated or result.user.ativo:
        flash("Consent confirmed. The account is active and ready to use.", FlashCategory.SUCCESS)
    else:
        flash("Consent confirmed. The account is waiting for email confirmation.", FlashCategory.SUCCESS)
    return _redirect_to(request, "arena_login")
