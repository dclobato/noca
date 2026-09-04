#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena login, logout, and LGPD age/parental-consent gate routes."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.database import get_db
from arena.models.arena_users import ArenaUser
from arena.routes.auth_common import (
    AUTH_RATE_LIMITER,
    _auth_rate_limit_settings,
    _login_failure_message,
    complete_arena_login,
    enforce_resend_throttle,
    record_email_delivery_event,
    render_login_gate_failure,
)
from arena.routes.auth_google_common import existing_account_next_path
from arena.routes.auth_throttle import throttled_response
from arena.services import arena_auth_service, user_email_service, user_service
from shared.age_check import AgeStatus, check_age
from shared.services.auth_rate_limit import (
    build_auth_throttle_identity,
    check_auth_throttle,
    record_auth_failure,
    reset_auth_throttle,
)
from shared.services.network_utils import NetworkService
from shared.services.request_rate_limit import get_client_ip
from shared.services.security_events import record_request_security_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["arena-auth"])


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _base_url(request: Request) -> str:
    """Return the public base URL used to build email links."""
    return settings.ARENA_URL_BASE or str(request.base_url).rstrip("/")


def _email_actor_key(request: Request) -> str:
    """Budget identity of an anonymous requester: the proxy-corrected client IP."""
    return f"ip:{get_client_ip(request) or 'unknown'}"


_LOGIN_FAILURE_REASONS: dict[user_service.UserOperationStatus, str] = {
    user_service.UserOperationStatus.USER_INACTIVE: "inactive",
    user_service.UserOperationStatus.PARENTAL_CONSENT_REQUIRED: "parental_consent_required",
    user_service.UserOperationStatus.AGE_RECONFIRMATION_REQUIRED: "age_reconfirmation_required",
    user_service.UserOperationStatus.UNDERAGE_BLOCKED: "underage_blocked",
}


def _redirect_to(request: Request, endpoint: str) -> RedirectResponse:
    """Build a 303 redirect to a named route endpoint."""
    return RedirectResponse(url=str(request.url_for(endpoint)), status_code=303)


def _parse_date_of_birth(value: str) -> date | None:
    """Parse an optional HTML date input value."""
    if not value:
        return None
    return date.fromisoformat(value)


def _user_needs_parental_consent(usuario: Any) -> bool:
    """Return True when an Arena user is in the 13-17 pending-consent gate."""
    if usuario.dta_nascimento is None:
        return False
    return check_age(usuario.dta_nascimento) == AgeStatus.NEEDS_PARENTAL_CONSENT and (
        not usuario.consentimento_responsavel
    )


def _arena_actor_label(user: Any | None) -> str | None:
    """Return the login identifier (email) used to identify an Arena actor."""
    if user is None:
        return None
    return getattr(user, "email_normalizado", None)


def _pending_parental_context(usuario: Any) -> dict[str, Any]:
    """Build the login template context for pending parental consent."""
    return {
        "show_resend_parental": bool(usuario.email_responsavel_legal),
        "show_parental_email_form": not bool(usuario.email_responsavel_legal),
        "masked_parental_email": usuario.email_responsavel_legal or "",
    }


@router.get("/login", response_class=HTMLResponse, name="arena_login")
async def arena_login(request: Request, next: str | None = None) -> HTMLResponse:
    """Render the Arena login page.

    While a Google-first signup is parked to be folded into an existing account
    (``pending_google_existing_uid``), an absent ``next`` defaults to that flow's
    confirmation page, so the ordinary post-login chain delivers the user there.
    """
    templates = request.app.state.arena_templates
    if next is None:
        next = existing_account_next_path(request)
    return _html(templates.TemplateResponse(request, "auth/login.html", {"next": next}))


@router.post("/login", name="arena_login_submit")
async def arena_login_submit(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    email: str = Form(""),
    password: str = Form(""),
    remember: str | None = Form(None),
    next: str | None = Form(None),
) -> Response:
    """Process the Arena login form."""
    templates = request.app.state.arena_templates
    jwt_service = request.app.state.jwt_service
    geo_service = request.app.state.geo_service
    remember_me = remember is not None
    ip_address = NetworkService.get_ip_from_request(request)
    source_port = NetworkService.get_trusted_source_port_from_request(request)
    user_agent = request.headers.get("User-Agent")
    throttle_settings = _auth_rate_limit_settings()
    throttle_identity = build_auth_throttle_identity(
        request,
        module="arena",
        action="login",
        identifier=email,
        settings=throttle_settings,
    )
    throttle_check = await check_auth_throttle(
        request,
        throttle_identity,
        settings=throttle_settings,
        fallback_limiter=AUTH_RATE_LIMITER,
    )
    if not throttle_check.allowed:
        await record_request_security_event(
            session,
            request,
            module="arena",
            event_type="auth_throttle_lockout",
            severity="warning",
            identifier_hash=throttle_identity.identifier_hash,
            metadata={"action": "login", "reason": throttle_check.reason},
        )
        await session.commit()
        flash("Too many failed attempts. Try again later.", FlashCategory.DANGER)
        return _html(
            templates.TemplateResponse(
                request,
                "auth/login.html",
                {"next": next},
                status_code=429,
                headers={
                    "Retry-After": str(throttle_check.retry_after_seconds or settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS)
                },
            )
        )

    result = await arena_auth_service.efetuar_login(
        email=email.strip(),
        password=password,
        session=session,
        ip_address=ip_address,
        source_port=source_port,
        user_agent=user_agent,
        geo_service=geo_service,
    )

    if result.status != user_service.UserOperationStatus.SUCCESS or result.user is None:
        await _record_login_failure(
            request,
            session,
            throttle_identity,
            "login",
            _LOGIN_FAILURE_REASONS.get(result.status, result.status.name),
            user=result.user,
        )
        gate_response = render_login_gate_failure(request, flash, templates=templates, result=result)
        if gate_response is not None:
            return gate_response
        return _redirect_to(request, "arena_login")

    usuario = result.user
    await reset_auth_throttle(
        request,
        throttle_identity,
        fallback_limiter=AUTH_RATE_LIMITER,
    )
    await session.commit()

    logger.info("Password authentication succeeded for user %s from %s", usuario.id, ip_address)
    return await complete_arena_login(
        request,
        session,
        flash,
        usuario=usuario,
        jwt_service=jwt_service,
        remember_me=remember_me,
        next_url=next,
        method="password",
    )


async def _record_login_failure(
    request: Request,
    session: AsyncSession,
    throttle_identity: Any,
    action: str,
    reason: str,
    user: Any | None = None,
) -> None:
    """Record a failed Arena login attempt and security event."""
    failure = await record_auth_failure(
        request,
        throttle_identity,
        settings=_auth_rate_limit_settings(),
        fallback_limiter=AUTH_RATE_LIMITER,
    )
    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type="auth_failure",
        severity="warning" if failure.locked else "info",
        actor_user_id=getattr(user, "id", None),
        actor_label=_arena_actor_label(user),
        identifier_hash=throttle_identity.identifier_hash,
        metadata={"action": action, "reason": reason, "lock_reason": failure.reason},
    )
    await session.commit()


@router.post("/resend-activation", name="arena_resend_activation")
async def arena_resend_activation(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Re-send the email confirmation link for an unconfirmed account."""
    retry_after = await enforce_resend_throttle(request, session, action="resend_activation")
    if retry_after is not None:
        flash("Too many requests. Please try again later.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")
    uid: str | None = request.session.pop("pending_resend_uid", None)
    if not uid:
        flash("No pending activation request found. Please log in to try again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    try:
        result = await user_email_service.revalidar_email(
            user_id=uid,
            session=session,
            jwt_service=request.app.state.jwt_service,
            email_service=request.app.state.email_service,
            url_base=_base_url(request),
            actor_key=_email_actor_key(request),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Activation email resend failed for user %s: %s", uid, exc)
        await record_email_delivery_event(
            session,
            request,
            user_id=uid,
            purpose="account_activation",
            source="resend",
            sent=False,
        )
        await session.commit()
        flash("We could not send the confirmation email right now. Please try again later.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")
    if result.user is not None and (
        result.email_sent or result.status == user_service.UserOperationStatus.SEND_EMAIL_ERROR
    ):
        await record_email_delivery_event(
            session,
            request,
            user_id=result.user.id,
            actor_label=_arena_actor_label(result.user),
            purpose="account_activation",
            source="resend",
            sent=result.email_sent,
        )
    await session.commit()

    if result.status == user_service.UserOperationStatus.SUCCESS:
        flash(
            "Confirmation email sent. Check your inbox and click the link to activate your account.",
            FlashCategory.SUCCESS,
        )
    elif result.status == user_service.UserOperationStatus.EMAIL_ALREADY_CONFIRMED:
        flash("Your email address is already confirmed. You can log in.", FlashCategory.INFO)
    else:
        flash("We could not send the confirmation email right now. Please try again later.", FlashCategory.DANGER)

    return _redirect_to(request, "arena_login")


@router.post("/resend-parental-consent", name="arena_resend_parental_consent")
async def arena_resend_parental_consent(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Re-send the parental consent link for a pending account."""
    retry_after = await enforce_resend_throttle(request, session, action="resend_parental_consent")
    if retry_after is not None:
        flash("Too many requests. Please try again later.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")
    uid: str | None = request.session.get("pending_parental_uid")
    if not uid:
        flash("No pending consent request found. Please log in to try again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    try:
        result = await user_email_service.revalidar_consentimento_responsavel(
            user_id=uid,
            session=session,
            jwt_service=request.app.state.jwt_service,
            email_service=request.app.state.email_service,
            url_base=_base_url(request),
            actor_key=_email_actor_key(request),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Parental-consent email resend failed for user %s: %s", uid, exc)
        await record_email_delivery_event(
            session,
            request,
            user_id=uid,
            purpose="parental_consent",
            source="resend",
            sent=False,
        )
        await session.commit()
        flash("We could not send the consent email right now. Please try again later.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")
    if result.user is not None and (
        result.email_sent or result.status == user_service.UserOperationStatus.SEND_EMAIL_ERROR
    ):
        await record_email_delivery_event(
            session,
            request,
            user_id=result.user.id,
            actor_label=_arena_actor_label(result.user),
            purpose="parental_consent",
            source="resend",
            sent=result.email_sent,
        )
    await session.commit()

    if result.status == user_service.UserOperationStatus.SUCCESS and result.email_sent:
        flash("Consent email sent. Ask your parent or legal guardian to check their inbox.", FlashCategory.SUCCESS)
    elif result.status == user_service.UserOperationStatus.SUCCESS:
        flash("Consent is already confirmed. Try logging in again.", FlashCategory.INFO)
    else:
        flash("We could not send the consent email right now. Please try again later.", FlashCategory.DANGER)
    return _redirect_to(request, "arena_login")


@router.post("/update-parental-email", name="arena_update_parental_email")
async def arena_update_parental_email(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    email_responsavel_legal: str = Form(""),
) -> Response:
    """Store or replace the parent/legal guardian email for a pending account."""
    retry_after = await enforce_resend_throttle(request, session, action="update_parental_email")
    if retry_after is not None:
        flash("Too many requests. Please try again later.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")
    uid: str | None = request.session.get("pending_parental_uid")
    if not uid:
        flash("No pending consent request found. Please log in to try again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    try:
        result = await user_email_service.atualizar_email_responsavel(
            user_id=uid,
            email_responsavel_legal=email_responsavel_legal.strip(),
            session=session,
            jwt_service=request.app.state.jwt_service,
            email_service=request.app.state.email_service,
            url_base=_base_url(request),
            actor_key=_email_actor_key(request),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Parental-consent email update failed for user %s: %s", uid, exc)
        await record_email_delivery_event(
            session,
            request,
            user_id=uid,
            purpose="parental_consent",
            source="update_parental_email",
            sent=False,
        )
        await session.commit()
        flash("We could not send the consent email right now. Please try again later.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")
    if result.user is not None and (
        result.email_sent or result.status == user_service.UserOperationStatus.SEND_EMAIL_ERROR
    ):
        await record_email_delivery_event(
            session,
            request,
            user_id=result.user.id,
            actor_label=_arena_actor_label(result.user),
            purpose="parental_consent",
            source="update_parental_email",
            sent=result.email_sent,
        )
    await session.commit()

    if result.status == user_service.UserOperationStatus.SUCCESS:
        flash("Consent email sent to the parent or legal guardian.", FlashCategory.SUCCESS)
    else:
        flash("Enter a valid parent or legal guardian email address.", FlashCategory.DANGER)
    return _redirect_to(request, "arena_login")


@router.post("/update-date-of-birth", name="arena_update_date_of_birth")
async def arena_update_date_of_birth(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    date_of_birth: str = Form(""),
) -> Response:
    """Regularise a legacy account that does not have a date of birth.

    A pending-session write with no secret to verify, so it carries only the
    per-IP attempt cap; the check runs after the pending-uid gate so a request
    with no pending flow never consumes the window.
    """
    uid: str | None = request.session.get("pending_age_uid")
    if not uid:
        flash("No pending age confirmation found. Please log in to try again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    retry_after = await enforce_resend_throttle(request, session, action="update_date_of_birth")
    if retry_after is not None:
        return throttled_response(request, flash, retry_after, back_route="arena_login")

    try:
        parsed_date_of_birth = _parse_date_of_birth(date_of_birth)
    except ValueError:
        flash("Enter a valid date of birth.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")
    if parsed_date_of_birth is None:
        flash("Enter your date of birth.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    result = await user_service.regularizar_data_nascimento(uid, parsed_date_of_birth, session)
    await session.commit()

    if result.status == user_service.UserOperationStatus.UNDERAGE_BLOCKED:
        flash(_login_failure_message(result.status), FlashCategory.DANGER)
    elif result.user is not None and _user_needs_parental_consent(result.user):
        request.session["pending_parental_uid"] = result.user.id
        flash("Parent or legal guardian consent is required before login.", FlashCategory.WARNING)
    else:
        request.session.pop("pending_age_uid", None)
        flash("Date of birth confirmed. You can log in now.", FlashCategory.SUCCESS)
    return _redirect_to(request, "arena_login")


@router.post("/logout", name="arena_logout")
async def arena_logout(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Log the current user out of the Arena.

    A pending Google-link marker is deliberately **left in place**. It names the
    account the link was started for, and the callback verifies that against the
    request's current authenticated user before attaching anything -- so a
    logged-out callback is refused outright. Clearing the marker here would make
    that same callback indistinguishable from an ordinary login, sending an
    unknown Google identity down the signup path and creating a stray second
    Arena account instead of a clean refusal.
    """
    jwt_service = request.app.state.jwt_service
    token: str | None = request.cookies.get("arena_access_token")
    actor_user_id: str | None = None
    actor_label: str | None = None

    if token:
        try:
            claims = jwt_service.validar(token)
            if claims.valid and claims.sub is not None:
                actor_user_id = claims.sub
        except Exception:
            actor_user_id = None
        if actor_user_id is not None:
            # The Arena token subject is the opaque user id; resolve the login
            # (email) so the security-event actor is human-readable, not hex.
            actor_label = (
                await session.execute(select(ArenaUser.email_normalizado).where(ArenaUser.id == actor_user_id))
            ).scalar_one_or_none()
        await arena_auth_service.efetuar_logout(token, jwt_service)
        logger.info("User logged out — token revoked")

    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type="auth_logout",
        actor_user_id=actor_user_id,
        actor_label=actor_label,
        metadata={"token_present": bool(token), "token_valid": actor_user_id is not None},
    )
    await session.commit()

    flash("You have been successfully logged out.", FlashCategory.SUCCESS)
    response = _redirect_to(request, "arena_dashboard")
    response.delete_cookie("arena_access_token", httponly=True, samesite="lax")
    return response
