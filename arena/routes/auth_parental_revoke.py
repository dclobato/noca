#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Guardian parental-consent revocation routes (LGPD art. 8 §5).

Two routes under ``/auth``, which the Arena access-control allowlist already treats as
public. They are split into their own module rather than added to ``auth_signup`` or
``auth``, both of which are already past the ``AGENTS.md`` size bound.

``GET`` only *renders* a confirmation page: mail scanners and link prefetchers follow
links in email, so the destructive half must be a ``POST`` the guardian submits. Both
halves resolve the token through the same
:func:`arena.services.parental_consent_service.resolve_revocation_token`, so the page and
the action can never disagree about whether a link may act, and the ``POST`` resolves it
again under a row lock before writing.

Every refusal renders one identical page. An unknown user, a stale consent epoch, consent
that was never granted, a child who has since turned 18, and a malformed token are
deliberately indistinguishable: anything else would let a stranger with a guessed link
learn whether an account exists or what state it is in.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.models.arena_users import ArenaUser
from arena.routes.auth_common import enforce_resend_throttle, record_email_delivery_event
from arena.services import parental_consent_service, user_service
from arena.services.consent_timeline import build_consent_timeline
from shared.services.request_rate_limit import get_client_ip
from shared.services.security_events import record_request_security_event

router = APIRouter(prefix="/auth", tags=["arena-auth"])
logger = logging.getLogger(__name__)

_THROTTLE_ACTION = "parental_consent_revoke"
_TEMPLATE = "auth/parental_consent_revoke.html"


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _email_actor_key(request: Request) -> str:
    """Budget identity of an anonymous requester: the proxy-corrected client IP."""
    return f"ip:{get_client_ip(request) or 'unknown'}"


def _render(
    request: Request,
    *,
    stage: str,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    **context: Any,
) -> HTMLResponse:
    """Render one stage of the revocation page.

    Args:
        request: Incoming request.
        stage: ``confirm``, ``done``, ``failed``, or ``throttled``.
        status_code: HTTP status for the response.
        headers: Extra response headers, such as ``Retry-After``.
        **context: Extra template variables for the stage.

    Returns:
        HTMLResponse: The rendered page.
    """
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            _TEMPLATE,
            {"stage": stage, **context},
            status_code=status_code,
            headers=headers,
        )
    )


def _failure(request: Request, log_reason: str | None) -> HTMLResponse:
    """Render the single generic failure page.

    The reason is logged and never rendered; see the module docstring for why every
    refusal has to look the same from outside.

    Args:
        request: Incoming request.
        log_reason: Server-side explanation of the refusal.

    Returns:
        HTMLResponse: The generic failure page.
    """
    logger.info("Parental-consent revocation link refused (%s)", log_reason or "unknown")
    return _render(request, stage="failed", status_code=200)


@router.get("/parental-consent/revoke", response_class=HTMLResponse, name="arena_parental_consent_revoke")
async def arena_parental_consent_revoke(
    request: Request,
    token: str = "",
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the revocation confirmation page for a guardian's link.

    Mutates nothing. The page names the child's full email address, alongside the
    chronology of the consent, so the guardian can be certain which account they are
    about to suspend. The address is not a disclosure this link makes: a valid token
    is issued to the guardian of that account and names the account it acts on, and a
    guardian who cannot confirm the address cannot safely confirm the decision.

    Args:
        request: Incoming request.
        token: Signed revocation token from the email link.
        session: Active async database session.

    Returns:
        HTMLResponse: The confirmation page, or the generic failure page.
    """
    resolved = await parental_consent_service.resolve_revocation_token(token, session, request.app.state.jwt_service)
    if resolved.user is None:
        return _failure(request, resolved.log_reason)
    return _render(
        request,
        stage="confirm",
        token=token,
        child_name=resolved.user.nome,
        child_email=resolved.user.email_normalizado,
        timeline=build_consent_timeline(resolved.user),
    )


@router.get("/parental-consent/revoked", response_class=HTMLResponse, name="arena_parental_consent_revoked")
async def arena_parental_consent_revoked(request: Request) -> HTMLResponse:
    """Render the post-revocation outcome page.

    The redirect target of a successful withdrawal, so the guardian never sits on a
    ``POST`` result: refreshing this page re-renders it instead of re-submitting a token
    that the revocation has already invalidated, which would otherwise answer the alarming
    "this link is no longer valid" page moments after the action succeeded.

    It deliberately takes no token and names no account. Everything it says is true of any
    completed withdrawal, so nothing has to travel through a URL -- keeping the child's
    name out of browser history, bookmarks and any ``Referer`` this page might send.

    Args:
        request: Incoming request.

    Returns:
        HTMLResponse: The outcome page.
    """
    return _render(request, stage="done")


@router.post("/parental-consent/revoke", name="arena_parental_consent_revoke_submit")
async def arena_parental_consent_revoke_submit(
    request: Request,
    token: str = Form(""),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Withdraw parental consent, suspending the account.

    Ordering matters in three places. The throttle runs first, so a link-guessing loop is
    capped before it can touch the database. The token is then resolved **again** under a
    row lock and the state change is applied inside it, so two guardians submitting at
    once cannot both pass validation -- the loser's epoch check fails against the value
    the winner committed. Finally the transaction commits *before* any email is queued: a
    provider refusal must not unwind a legally effective revocation, and a queued message
    must never describe one that failed to commit.

    A success answers ``303`` to :func:`arena_parental_consent_revoked` rather than
    rendering inline, so a refresh cannot re-submit a token the revocation has already
    invalidated. Only the success path redirects: a refused submission is not a state
    change, and re-sending it simply fails again in exactly the same way.

    Args:
        request: Incoming request.
        token: Signed revocation token from the confirmation form.
        session: Active async database session.

    Returns:
        Response: A ``303`` to the outcome page, or the generic failure page.
    """
    retry_after = await enforce_resend_throttle(request, session, action=_THROTTLE_ACTION)
    if retry_after is not None:
        return _render(
            request,
            stage="throttled",
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    resolved = await parental_consent_service.resolve_revocation_token(
        token, session, request.app.state.jwt_service, lock=True
    )
    usuario = resolved.user
    if usuario is None:
        return _failure(request, resolved.log_reason)

    guardian_email = usuario.email_responsavel_legal
    await user_service.revoke_parental_consent(usuario, session)
    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type="parental_consent_revoked",
        severity="warning",
        actor_user_id=usuario.id,
        actor_label=guardian_email,
        metadata={"source": "guardian_token"},
    )
    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type="account_deactivated",
        severity="warning",
        actor_user_id=usuario.id,
        actor_label=guardian_email,
        metadata={"source": "parental_consent_revocation"},
    )
    await session.commit()

    await _notify_revocation(request, session, usuario)
    return RedirectResponse(url=str(request.url_for("arena_parental_consent_revoked")), status_code=303)


async def _notify_revocation(request: Request, session: AsyncSession, usuario: ArenaUser) -> None:
    """Tell the guardian and the child that the account was suspended.

    Each recipient is attempted independently so one spent budget or provider refusal
    cannot silence the other, and both outcomes are recorded after the revocation has
    already committed.

    Args:
        request: Incoming request.
        session: Active async database session.
        usuario: The suspended account.
    """
    actor_key = _email_actor_key(request)
    for recipient in ("guardian", "child"):
        try:
            sent = await parental_consent_service.send_consent_revoked_email(
                usuario,
                recipient=recipient,
                email_service=request.app.state.email_service,
                actor_key=actor_key,
            )
        except Exception as exc:  # noqa: BLE001 -- house pattern: delivery never fails a route
            logger.warning("Consent-revoked email to %s failed for user %s: %s", recipient, usuario.id, exc)
            sent = False
        await record_email_delivery_event(
            session,
            request,
            user_id=usuario.id,
            actor_label=usuario.email_normalizado,
            purpose="parental_consent_revoked",
            source=f"guardian_token:{recipient}",
            sent=sent,
        )
    await session.commit()
