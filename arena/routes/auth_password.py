#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena password-change and password-reset routes."""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.routes.auth_common import (
    AUTH_RATE_LIMITER,
    _auth_rate_limit_settings,
    _issue_login_token,
    _set_login_cookie,
    _token_failure_message,
    _validate_password_fields,
    _validated_login_session_started_at,
    _validated_login_uses_remember_me,
)
from arena.services import arena_auth_service, arena_password_service, user_security_notification_service, user_service
from arena.services.session_service import post_login_redirect_url
from arena.services.token_service import ArenaTokenAction
from shared.services.auth_rate_limit import (
    build_auth_throttle_identity,
    check_auth_throttle,
    record_auth_failure,
    reset_auth_throttle,
)
from shared.services.password_service import PasswordPolicy
from shared.services.security_events import record_request_security_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["arena-auth"])


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _redirect_to(request: Request, endpoint: str) -> RedirectResponse:
    """Build a 303 redirect to a named route endpoint."""
    return RedirectResponse(url=str(request.url_for(endpoint)), status_code=303)


def _base_url(request: Request) -> str:
    """Return the public base URL used to build email links."""
    return settings.ARENA_URL_BASE or str(request.base_url).rstrip("/")


@router.get("/change-password", response_class=HTMLResponse, name="arena_change_password")
async def arena_change_password(
    request: Request,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
) -> Response:
    """Render the password-change page (forced or voluntary mode)."""
    has_pending = bool(request.session.get("pending_pw_change_token"))
    if not has_pending and current_user is None:
        return _redirect_to(request, "arena_login")
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "auth/password_change.html",
            {"password_hint": PasswordPolicy(settings).policy_hint},
        )
    )


@router.post("/change-password", name="arena_change_password_submit")
async def arena_change_password_submit(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
) -> Response:
    """Process the password-change form (forced or voluntary mode)."""
    jwt_service = request.app.state.jwt_service
    raw_token: str = request.session.get("pending_pw_change_token", "")

    if raw_token:
        result = await arena_auth_service.get_pending_password_change_token_data(raw_token, session, jwt_service)
        if result.status != user_service.UserOperationStatus.SUCCESS or result.user is None:
            flash(_token_failure_message(result.status), FlashCategory.DANGER)
            return _redirect_to(request, "arena_login")
        usuario = result.user
        extra_data: dict[str, Any] = result.extra_data or {}
        forced = True
    elif current_user is not None:
        usuario = current_user
        extra_data = {}
        forced = False
    else:
        flash("Session expired. Please log in again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    if not usuario.check_password(current_password):
        flash("Current password is incorrect.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_change_password")

    password_error = _validate_password_fields(new_password, confirm_password)
    if password_error is not None:
        flash(password_error, FlashCategory.DANGER)
        return _redirect_to(request, "arena_change_password")

    usuario.password = new_password
    await session.flush()

    if not forced:
        await session.commit()
        # Re-issue the login cookie so the updated tid (session_version incremented
        # by the password setter) stays in sync — otherwise the next request triggers
        # a force-logout via the tid mismatch check in get_current_arena_user.
        remembered_session = _validated_login_uses_remember_me(request)
        session_started_at = _validated_login_session_started_at(request)
        token_refresh = _issue_login_token(
            jwt_service=jwt_service,
            usuario=usuario,
            remember_me=remembered_session,
            session_started_at=session_started_at,
        )
        if not user_security_notification_service.send_password_changed_email(usuario, request.app.state.email_service):
            logger.warning("Password-changed notification email failed for user %s", usuario.id)
        flash("Password changed successfully.", FlashCategory.SUCCESS)
        logger.info("Voluntary password change for user %s", usuario.id)
        response = _redirect_to(request, "arena_user_profile")
        _set_login_cookie(response, token=token_refresh, remember_me=remembered_session)
        return response

    del request.session["pending_pw_change_token"]
    remember_me: bool = bool(extra_data.get("remember_me", False))
    next_url: str | None = extra_data.get("next")
    session_started_at = extra_data.get("session_started_at")
    if not isinstance(session_started_at, int):
        session_started_at = None
    token_login = _issue_login_token(
        jwt_service=jwt_service,
        usuario=usuario,
        remember_me=remember_me,
        session_started_at=session_started_at,
    )
    await session.commit()

    if not user_security_notification_service.send_password_changed_email(usuario, request.app.state.email_service):
        logger.warning("Password-changed notification email failed for user %s", usuario.id)
    logger.info("Forced password change completed for user %s", usuario.id)
    flash("Password changed successfully.", FlashCategory.SUCCESS)
    response = RedirectResponse(url=post_login_redirect_url(usuario, next_url, request), status_code=303)
    _set_login_cookie(response, token=token_login, remember_me=remember_me)
    return response


@router.get("/password-reset", response_class=HTMLResponse, name="arena_password_reset")
async def arena_password_reset(request: Request, flash: FlashDep, token: str = "") -> Response:
    """Render the password reset request form or the new-password form."""
    templates = request.app.state.arena_templates
    if token:
        claims = request.app.state.jwt_service.validar(token)
        if not claims.valid or claims.action != ArenaTokenAction.RESET_PASSWORD:
            status = (
                user_service.UserOperationStatus.TOKEN_EXPIRED
                if getattr(claims, "reason", None) == "expired"
                else user_service.UserOperationStatus.INVALID_TOKEN
            )
            flash(_token_failure_message(status), FlashCategory.DANGER)
            return _redirect_to(request, "arena_login")
    return _html(
        templates.TemplateResponse(
            request,
            "auth/password_reset.html",
            {"token": token, "password_hint": PasswordPolicy(settings).policy_hint},
        )
    )


@router.post("/password-reset", name="arena_password_reset_submit")
async def arena_password_reset_submit(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    email: str = Form(""),
    token: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
) -> Response:
    """Send a password reset email or replace the password for a reset token."""
    throttle_settings = _auth_rate_limit_settings()
    throttle_identifier = token if token else email
    throttle_identity = build_auth_throttle_identity(
        request,
        module="arena",
        action="password-reset",
        identifier=throttle_identifier,
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
            metadata={"action": "password-reset", "reason": throttle_check.reason},
        )
        await session.commit()
        flash("Too many failed attempts. Try again later.", FlashCategory.DANGER)
        return _html(
            request.app.state.arena_templates.TemplateResponse(
                request,
                "auth/password_reset.html",
                {"token": token, "password_hint": PasswordPolicy(settings).policy_hint},
                status_code=429,
                headers={"Retry-After": str(throttle_check.retry_after_seconds or throttle_settings.lockout_seconds)},
            )
        )

    if not token:
        await arena_password_service.solicitar_reset_senha(
            email=email.strip(),
            session=session,
            jwt_service=request.app.state.jwt_service,
            email_service=request.app.state.email_service,
            url_base=_base_url(request),
        )
        failure = await record_auth_failure(
            request,
            throttle_identity,
            settings=throttle_settings,
            fallback_limiter=AUTH_RATE_LIMITER,
        )
        await record_request_security_event(
            session,
            request,
            module="arena",
            event_type="password_reset_request",
            severity="warning" if failure.locked else "info",
            identifier_hash=throttle_identity.identifier_hash,
            metadata={"action": "password-reset", "reason": failure.reason},
        )
        await session.commit()
        flash("If an account exists for that email, a reset link has been sent.", FlashCategory.SUCCESS)
        return _redirect_to(request, "arena_login")

    password_error = _validate_password_fields(password, confirm_password)
    if password_error is not None:
        flash(password_error, FlashCategory.DANGER)
        return RedirectResponse(url=str(request.url_for("arena_password_reset")) + f"?token={token}", status_code=303)

    result = await arena_password_service.redefinir_senha_por_token(
        token=token,
        nova_senha=password,
        session=session,
        jwt_service=request.app.state.jwt_service,
    )
    if result.status == user_service.UserOperationStatus.SUCCESS:
        reset_identity = build_auth_throttle_identity(
            request,
            module="arena",
            action="password-reset",
            identifier=result.user.email_normalizado if result.user is not None else token,
            settings=throttle_settings,
        )
        await reset_auth_throttle(request, reset_identity, fallback_limiter=AUTH_RATE_LIMITER)
        await session.commit()
        flash("Password reset successfully. You can log in with the new password.", FlashCategory.SUCCESS)
        return _redirect_to(request, "arena_login")

    failure = await record_auth_failure(
        request,
        throttle_identity,
        settings=throttle_settings,
        fallback_limiter=AUTH_RATE_LIMITER,
    )
    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type="suspicious_token_mismatch",
        severity="warning",
        actor_user_id=result.user.id if result.user is not None else None,
        actor_label=result.user.email_normalizado if result.user is not None else None,
        identifier_hash=throttle_identity.identifier_hash,
        metadata={"action": "password-reset", "reason": result.status.name, "lock_reason": failure.reason},
    )
    await session.commit()
    flash(_token_failure_message(result.status), FlashCategory.DANGER)
    return _redirect_to(request, "arena_login")
