#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Shared helpers for Arena authentication routes.

Not a route module — contains no router.  Import from this module in the
individual auth route files to avoid duplicating non-trivial logic.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.models.arena_users import ArenaUser
from arena.services import arena_auth_service, parental_consent_service
from arena.services.session_service import (
    ARENA_REMEMBER_ME_MAX_AGE,
    build_login_token_extra_data,
    get_session_started_at,
    is_remembered_login,
    safe_next_url,
)
from arena.services.token_service import ArenaTokenAction
from arena.services.user_service import UserOperationStatus, UserServiceResult
from shared.age_check import AgeStatus, check_age
from shared.services.auth_rate_limit import (
    AuthRateLimitSettings,
    InMemoryAuthRateLimiter,
    build_auth_throttle_identity,
    check_auth_throttle,
    record_auth_failure,
)
from shared.services.password_service import PasswordPolicy, PasswordPolicyError
from shared.services.request_rate_limit import InMemoryRateLimiter, RateLimitPolicy, get_client_ip
from shared.services.security_events import record_request_security_event

logger = logging.getLogger(__name__)

_REMEMBER_ME_MAX_AGE = ARENA_REMEMBER_ME_MAX_AGE
AUTH_RATE_LIMITER = InMemoryAuthRateLimiter()
SIGNUP_RATE_LIMITER = InMemoryRateLimiter()
SIGNUP_REQUEST_RATE_LIMITER = InMemoryRateLimiter()
SIGNUP_RATE_LIMIT_BUCKET = "arena:signup"
SIGNUP_REQUEST_RATE_LIMIT_BUCKET = "arena:signup-requests"


def _auth_rate_limit_settings() -> AuthRateLimitSettings:
    """Build Arena auth-throttle settings from config."""
    return AuthRateLimitSettings(
        enabled=settings.AUTH_RATE_LIMIT_ENABLED,
        window_seconds=settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
        ip_max_failures=settings.AUTH_RATE_LIMIT_IP_MAX_FAILURES,
        account_max_failures=settings.AUTH_RATE_LIMIT_ACCOUNT_MAX_FAILURES,
        lockout_seconds=settings.AUTH_RATE_LIMIT_LOCKOUT_SECONDS,
        secret=settings.JWT_SECRET_KEY,
    )


def _signup_rate_limit_policy() -> RateLimitPolicy:
    """Build the per-IP signup *attempt* window policy from config.

    This is the small budget (5/hour by default) that pays for the expensive
    half of a signup: the account-existence lookup, the paid IPQualityScore
    calls, the user insert and the activation email. It is therefore spent only
    by a submission that reaches them -- a request rejected by form validation
    costs the deployment nothing and must not consume it, or five mistyped
    password confirmations would lock a whole school lab out of registering.
    Raw flooding is bounded separately by :func:`_signup_request_rate_limit_policy`.
    No trusted network bypasses it: a local probe has no reason to sign up.
    """
    return RateLimitPolicy(
        bucket=SIGNUP_RATE_LIMIT_BUCKET,
        max_requests=settings.SIGNUP_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.SIGNUP_RATE_LIMIT_WINDOW_SECONDS,
        trusted_networks=(),
        enabled=settings.AUTH_RATE_LIMIT_ENABLED,
    )


def _signup_request_rate_limit_policy() -> RateLimitPolicy:
    """Build the per-IP signup *request* flood guard from config.

    Counted before any validation, over the same window as the attempt budget
    but with a much higher ceiling: parsing a multipart submission with a photo
    is not free, so unlimited invalid requests are still worth bounding -- but
    the two caps answer two different questions, and a shared address must be
    able to fumble the form far more often than it can reach the paid work.
    """
    return RateLimitPolicy(
        bucket=SIGNUP_REQUEST_RATE_LIMIT_BUCKET,
        max_requests=settings.SIGNUP_REQUEST_RATE_LIMIT_MAX_REQUESTS,
        window_seconds=settings.SIGNUP_RATE_LIMIT_WINDOW_SECONDS,
        trusted_networks=(),
        enabled=settings.AUTH_RATE_LIMIT_ENABLED,
    )


async def record_email_delivery_event(
    session: AsyncSession,
    request: Request,
    *,
    user_id: str | None,
    actor_label: str | None = None,
    purpose: str,
    source: str,
    sent: bool,
    severity: str | None = None,
) -> None:
    """Record a security event for an account-lifecycle email delivery attempt.

    Args:
        session: Active async database session.
        request: Incoming request, for client metadata.
        user_id: Subject account id when known.
        actor_label: Human-readable login of the actor.
        purpose: Email purpose slug, e.g. ``parental_consent``.
        source: Flow that triggered the send, e.g. ``signup``.
        sent: Whether the message was accepted for delivery.
        severity: Override for the failure severity. Defaults to ``info`` on success and
            ``warning`` on failure; a caller passes ``warning`` explicitly when a *lost*
            message costs the user something they cannot recover on their own.
    """
    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type=f"{purpose}_email_{'sent' if sent else 'failed'}",
        severity=severity or ("info" if sent else "warning"),
        actor_user_id=user_id,
        actor_label=actor_label,
        metadata={"purpose": purpose, "source": source},
    )


async def enforce_resend_throttle(
    request: Request,
    session: AsyncSession,
    *,
    action: str,
) -> int | None:
    """Rate-limit a pending-session action by client IP.

    The email-resend routes send an email on every request, and the
    pending-flow writes (``update-date-of-birth``, ``accept-terms``) mutate an
    account; all are gated only by a Starlette session key, so they need an
    IP-scoped abuse cap independent of any account identifier. The identity is
    built with ``identifier=None`` so only the IP bucket is used, and every
    request counts, whatever its outcome.

    Args:
        request: Incoming request.
        session: Active async database session (committed when a lockout is
            recorded).
        action: Stable throttle action slug for this resend flow.

    Returns:
        The retry-after seconds when the caller is currently locked out;
        otherwise records one attempt against the IP bucket and returns
        ``None``.
    """
    throttle_settings = _auth_rate_limit_settings()
    identity = build_auth_throttle_identity(
        request,
        module="arena",
        action=action,
        identifier=None,
        settings=throttle_settings,
    )
    check = await check_auth_throttle(
        request,
        identity,
        settings=throttle_settings,
        fallback_limiter=AUTH_RATE_LIMITER,
    )
    if not check.allowed:
        await record_request_security_event(
            session,
            request,
            module="arena",
            event_type="auth_throttle_lockout",
            severity="warning",
            metadata={"action": action, "reason": check.reason},
        )
        await session.commit()
        return check.retry_after_seconds or throttle_settings.lockout_seconds
    await record_auth_failure(
        request,
        identity,
        settings=throttle_settings,
        fallback_limiter=AUTH_RATE_LIMITER,
    )
    return None


def _login_token_expires_in() -> int:
    """Return the LOGIN token lifetime used for Arena session JWTs."""
    return settings.JWT_EXPIRE_SECONDS


def _login_token_extra_data(
    usuario: ArenaUser,
    *,
    remember_me: bool,
    session_started_at: int | None = None,
) -> dict[str, Any]:
    """Build the LOGIN token extra_data payload."""
    return build_login_token_extra_data(
        tid=usuario.get_token_id(),
        remember_me=remember_me,
        session_started_at=session_started_at,
    )


def _issue_login_token(
    *,
    jwt_service: Any,
    usuario: ArenaUser,
    remember_me: bool,
    session_started_at: int | None = None,
) -> str:
    """Create a signed Arena LOGIN token with remember-me-aware expiry."""
    return str(
        jwt_service.criar(
            action=ArenaTokenAction.LOGIN,
            sub=usuario.id,
            expires_in=_login_token_expires_in(),
            extra_data=_login_token_extra_data(
                usuario,
                remember_me=remember_me,
                session_started_at=session_started_at,
            ),
        )
    )


def _set_login_cookie(response: Response, *, token: str, remember_me: bool) -> None:
    """Attach the Arena login cookie with the correct persistence flags."""
    cookie_kwargs: dict[str, Any] = {
        "key": "arena_access_token",
        "value": token,
        "httponly": True,
        "samesite": "lax",
        "secure": settings.COOKIE_SECURE,
    }
    if remember_me:
        cookie_kwargs["max_age"] = _REMEMBER_ME_MAX_AGE
    response.set_cookie(**cookie_kwargs)


def _validated_login_uses_remember_me(request: Request) -> bool:
    """Return whether the current validated Arena LOGIN token is remembered."""
    validation = getattr(request.state, "validated_token", None)
    return is_remembered_login(validation)


def _validated_login_session_started_at(request: Request) -> int | None:
    """Return the original remembered-session start timestamp from the validated LOGIN token."""
    validation = getattr(request.state, "validated_token", None)
    return get_session_started_at(validation)


def _flash_password_age_warning(usuario: Any, flash: FlashDep) -> None:
    """Flash a warning if the user's password exceeds the configured maximum age."""
    max_age = settings.ARENA_PASSWORD_MAX_AGE
    if max_age <= 0:
        return
    last_changed = usuario.dta_ultima_alteracao_senha
    if last_changed is None:
        return
    if last_changed.tzinfo is None:
        last_changed = last_changed.replace(tzinfo=UTC)
    age_days = (datetime.now(UTC) - last_changed).days
    if age_days >= max_age:
        flash(
            f"Your password is {age_days} days old. Consider updating it from your profile.",
            FlashCategory.WARNING,
        )


def _token_failure_message(status: UserOperationStatus) -> str:
    """Map token service statuses to user-facing messages."""
    if status == UserOperationStatus.TOKEN_EXPIRED:
        return "This link has expired. Please request a new one."
    if status == UserOperationStatus.USER_NOT_FOUND:
        return "We could not find the account for this link."
    if status == UserOperationStatus.EMAIL_ALREADY_CONFIRMED:
        return "Your email address is already confirmed. You can log in."
    return "This link is invalid. Please request a new one."


def _login_failure_message(status: UserOperationStatus) -> str:
    """Map login service statuses to user-facing messages."""
    if status == UserOperationStatus.DATABASE_ERROR:
        return "A server error occurred. Please try again."
    if status == UserOperationStatus.PARENTAL_CONSENT_REQUIRED:
        return "Parent or legal guardian consent is required before you can log in."
    if status == UserOperationStatus.AGE_RECONFIRMATION_REQUIRED:
        return "Confirm your date of birth before logging in."
    if status == UserOperationStatus.UNDERAGE_BLOCKED:
        return "The platform is not intended for users under 13 years old."
    return "Invalid email or password."


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _user_needs_parental_consent(usuario: Any) -> bool:
    """Return True when an Arena user is in the 13-17 pending-consent gate."""
    if usuario.dta_nascimento is None:
        return False
    return check_age(usuario.dta_nascimento) == AgeStatus.NEEDS_PARENTAL_CONSENT and (
        not usuario.consentimento_responsavel
    )


def _pending_parental_context(usuario: Any) -> dict[str, Any]:
    """Build the login template context for pending parental consent."""
    return {
        "show_resend_parental": bool(usuario.email_responsavel_legal),
        "show_parental_email_form": not bool(usuario.email_responsavel_legal),
        "masked_parental_email": usuario.email_responsavel_legal or "",
    }


def render_login_gate_failure(
    request: Request,
    flash: FlashDep,
    *,
    templates: Any,
    result: UserServiceResult,
) -> Response | None:
    """Route a blocked login to the remediation flow its status calls for.

    Shared by the password form and the Google callback so both doors send a
    user to the same place: an unconfirmed email to the resend prompt, a pending
    minor to the guardian-consent prompt, an account with no date of birth to
    the reconfirmation prompt, and a blocked or deactivated account back to the
    login page. A second door that routed these differently -- or not at all --
    is how an account gets in past a gate it should have been held at.

    The caller records the failure (each door throttles under its own action)
    before calling this.

    Args:
        request: The active request.
        flash: Flash-message dependency.
        templates: The Arena Jinja templates object.
        result: The blocking result from evaluate_account_access_gates, or any
            other non-SUCCESS login result.

    Returns:
        Response | None: The response to return, or ``None`` when the status is
            SUCCESS and there is nothing to route.
    """
    usuario = result.user

    if result.status == UserOperationStatus.SUCCESS:
        return None

    if result.status == UserOperationStatus.USER_INACTIVE:
        if usuario is not None and not usuario.email_confirmado:
            request.session["pending_resend_uid"] = usuario.id
            flash(
                "Your email address has not been confirmed. Check your inbox or request a new link below.",
                FlashCategory.WARNING,
            )
            return _html(
                templates.TemplateResponse(
                    request,
                    "auth/login.html",
                    {"show_resend": True},
                    status_code=200,
                )
            )
        if usuario is not None and _user_needs_parental_consent(usuario):
            request.session["pending_parental_uid"] = usuario.id
            flash(
                "This account is waiting for parent or legal guardian consent.",
                FlashCategory.WARNING,
            )
            return _html(
                templates.TemplateResponse(
                    request,
                    "auth/login.html",
                    _pending_parental_context(usuario),
                    status_code=200,
                )
            )
        flash("Your account has been deactivated. Please contact support.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    if result.status == UserOperationStatus.PARENTAL_CONSENT_REQUIRED and usuario is not None:
        request.session["pending_parental_uid"] = usuario.id
        flash(_login_failure_message(result.status), FlashCategory.WARNING)
        return _html(
            templates.TemplateResponse(
                request,
                "auth/login.html",
                _pending_parental_context(usuario),
                status_code=200,
            )
        )

    if result.status == UserOperationStatus.AGE_RECONFIRMATION_REQUIRED and usuario is not None:
        request.session["pending_age_uid"] = usuario.id
        flash(_login_failure_message(result.status), FlashCategory.WARNING)
        return _html(
            templates.TemplateResponse(
                request,
                "auth/login.html",
                {"show_age_reconfirmation": True},
                status_code=200,
            )
        )

    flash(_login_failure_message(result.status), FlashCategory.DANGER)
    return _redirect_to(request, "arena_login")


def _redirect_to(request: Request, endpoint: str) -> RedirectResponse:
    """Build a 303 redirect to a named route endpoint."""
    return RedirectResponse(url=str(request.url_for(endpoint)), status_code=303)


async def complete_arena_login(
    request: Request,
    session: AsyncSession,
    flash: FlashDep,
    *,
    usuario: ArenaUser,
    jwt_service: Any,
    remember_me: bool,
    next_url: str | None,
    method: str,
) -> Response:
    """Run the post-authentication gate chain and issue the Arena session.

    Every way of proving an Arena identity converges here once the proof itself
    has succeeded: the password form and the Google callback both call this, so
    a new authentication door cannot quietly skip the terms gate, the 2FA gate,
    or the forced-password-change gate. Duplicating this chain is precisely how
    such a gate goes missing.

    The account-state gates (active, date of birth known, LGPD age rules) are
    *not* here -- they belong before the identity is accepted and live in
    ``arena_auth_service.evaluate_account_access_gates``, which both callers run.

    This does not record login history. That call is asymmetric in the existing
    code (``efetuar_login`` makes it for the password path, ``auth_2fa`` for the
    2FA path), and each caller therefore keeps ownership of it.

    Args:
        request: The active request.
        session: Active async database session; committed by this function.
        flash: Flash-message dependency.
        usuario: The user whose identity has been proven.
        jwt_service: Arena JWT service used to mint the session and gate tokens.
        remember_me: Whether the user asked for a remembered session.
        next_url: Caller-supplied post-login target; re-validated by safe_next_url.
        method: How identity was proven -- ``"password"`` or ``"google"``. Recorded
            in the auth_success event and carried through a 2FA hop so the login
            history can distinguish ``2fa`` from ``google_2fa``.

    Returns:
        Response: A 303 to a pending gate, or to the post-login target with the
            ``arena_access_token`` cookie set.
    """
    if not usuario.aceitou_termos_privacidade:
        request.session["pending_tos_uid"] = str(usuario.id)
        flash(
            "Please review and accept our Terms of Service and Privacy Policy to continue.",
            FlashCategory.WARNING,
        )
        return _redirect_to(request, "arena_accept_terms")

    session_started_at = int(datetime.now(UTC).timestamp()) if remember_me else None

    if usuario.usa_2fa:
        token_2fa = arena_auth_service.set_pending_2fa_token(
            usuario,
            jwt_service,
            remember_me=remember_me,
            next_page=next_url,
            session_started_at=session_started_at,
            login_method=method,
        )
        request.session["pending_2fa_token"] = token_2fa
        logger.info("2FA required for user %s — redirecting to 2FA page", usuario.id)
        return _redirect_to(request, "arena_2fa")

    # An account with no usable password has nothing to change, and sending it to
    # the change-password form would trap it there: that form requires the current
    # password, which by construction cannot be supplied.
    if usuario.precisa_trocar_senha and usuario.has_usable_password:
        token_pw = arena_auth_service.set_pending_password_change_token(
            usuario,
            jwt_service,
            remember_me=remember_me,
            next_page=next_url,
            session_started_at=session_started_at,
        )
        request.session["pending_pw_change_token"] = token_pw
        logger.info(
            "Forced password change for user %s — redirecting to change-password page",
            usuario.id,
        )
        return _redirect_to(request, "arena_change_password")

    _flash_password_age_warning(usuario, flash)

    token = _issue_login_token(
        jwt_service=jwt_service,
        usuario=usuario,
        remember_me=remember_me,
        session_started_at=session_started_at,
    )

    logger.info("Successful %s login for user %s", method, usuario.id)
    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type="auth_success",
        actor_user_id=usuario.id,
        actor_label=usuario.email_normalizado,
        metadata={"action": "login", "method": method, "remember_me": remember_me},
    )
    await session.commit()
    response = RedirectResponse(url=safe_next_url(next_url, request), status_code=303)
    _set_login_cookie(response, token=token, remember_me=remember_me)
    return response


def _validate_password_fields(password: str, confirm_password: str) -> str | None:
    """Validate password confirmation and configured password policy."""
    if password != confirm_password:
        return "Passwords do not match."
    try:
        PasswordPolicy(settings).validate_new_password(password)
    except PasswordPolicyError as exc:
        return str(exc)
    return None


def _consent_base_url(request: Request) -> str:
    """Return the public base URL used to build email links."""
    return settings.ARENA_URL_BASE or str(request.base_url).rstrip("/")


def _consent_actor_key(request: Request) -> str:
    """Budget identity of an anonymous requester: the proxy-corrected client IP."""
    return f"ip:{get_client_ip(request) or 'unknown'}"


async def send_consent_confirmation_email(request: Request, session: AsyncSession, usuario: ArenaUser) -> None:
    """Confirm the grant to the guardian and hand them the revocation link.

    Called only after the grant has committed, so the link it carries holds the post-grant
    consent epoch rather than one the commit is about to invalidate. A failed delivery is
    recorded at **warning** severity because this message is the only carrier of that
    link: losing it costs the guardian their self-service withdrawal until an
    administrator intervenes.

    Args:
        request: Incoming request.
        session: Active async database session.
        usuario: Arena user whose consent was just granted.
    """
    try:
        sent = await parental_consent_service.send_consent_confirmed_email(
            usuario,
            jwt_service=request.app.state.jwt_service,
            email_service=request.app.state.email_service,
            url_base=_consent_base_url(request),
            actor_key=_consent_actor_key(request),
        )
    except Exception as exc:  # noqa: BLE001 -- house pattern: delivery never fails a route
        logger.warning("Consent-confirmed email failed for user %s: %s", usuario.id, exc)
        sent = False
    await record_email_delivery_event(
        session,
        request,
        user_id=usuario.id,
        actor_label=usuario.email_responsavel_legal,
        purpose="parental_consent_confirmed",
        source="parental_consent_token",
        sent=sent,
        severity="info" if sent else "warning",
    )
    await session.commit()
