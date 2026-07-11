#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena two-factor authentication routes."""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.routes.auth_common import (
    AUTH_RATE_LIMITER,
    _auth_rate_limit_settings,
    _flash_password_age_warning,
    _issue_login_token,
    _set_login_cookie,
    _token_failure_message,
)
from arena.services import arena_auth_service, user_2fa_service, user_security_notification_service, user_service
from arena.services.session_service import post_login_redirect_url
from shared.services.auth_rate_limit import (
    build_auth_throttle_identity,
    check_auth_throttle,
    record_auth_failure,
    reset_auth_throttle,
)
from shared.services.network_utils import NetworkService
from shared.services.security_events import record_request_security_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["arena-auth"])


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _redirect_to(request: Request, endpoint: str) -> RedirectResponse:
    """Build a 303 redirect to a named route endpoint."""
    return RedirectResponse(url=str(request.url_for(endpoint)), status_code=303)


@router.get("/2fa", response_class=HTMLResponse, name="arena_2fa")
async def arena_2fa(request: Request) -> HTMLResponse:
    """Render the Arena two-factor authentication page."""
    templates = request.app.state.arena_templates
    return _html(templates.TemplateResponse(request, "auth/two_factor.html", {}))


@router.post("/2fa", name="arena_2fa_submit")
async def arena_2fa_submit(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    full_code: str = Form(""),
) -> Response:
    """Process the 2FA verification form."""
    jwt_service = request.app.state.jwt_service

    token: str | None = request.session.get("pending_2fa_token")
    if not token:
        flash("Session expired. Please log in again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    result = await arena_auth_service.get_pending_2fa_token_data(token, session, jwt_service)
    if result.status != user_service.UserOperationStatus.SUCCESS or result.user is None:
        await record_request_security_event(
            session,
            request,
            module="arena",
            event_type="suspicious_token_mismatch",
            severity="warning",
            metadata={"action": "2fa", "reason": result.status.name},
        )
        await session.commit()
        flash(_token_failure_message(result.status), FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    usuario = result.user
    extra_data: dict[str, Any] = result.extra_data or {}
    throttle_settings = _auth_rate_limit_settings()
    throttle_identity = build_auth_throttle_identity(
        request,
        module="arena",
        action="2fa",
        identifier=usuario.email_normalizado,
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
            actor_user_id=usuario.id,
            actor_label=usuario.email_normalizado,
            identifier_hash=throttle_identity.identifier_hash,
            metadata={"action": "2fa", "reason": throttle_check.reason},
        )
        await session.commit()
        flash("Too many failed attempts. Try again later.", FlashCategory.DANGER)
        return _html(
            request.app.state.arena_templates.TemplateResponse(
                request,
                "auth/two_factor.html",
                {},
                status_code=429,
                headers={"Retry-After": str(throttle_check.retry_after_seconds or throttle_settings.lockout_seconds)},
            )
        )

    validation = await user_2fa_service.validar_codigo_2fa(usuario, full_code.strip(), session)
    if not validation.success:
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
            event_type="auth_failure",
            severity="warning" if failure.locked else "info",
            actor_user_id=usuario.id,
            actor_label=usuario.email_normalizado,
            identifier_hash=throttle_identity.identifier_hash,
            metadata={"action": "2fa", "reason": failure.reason},
        )
        await session.commit()
        flash("Invalid or expired code. Please try again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_2fa")

    del request.session["pending_2fa_token"]
    await reset_auth_throttle(
        request,
        throttle_identity,
        fallback_limiter=AUTH_RATE_LIMITER,
    )

    remember_me: bool = bool(extra_data.get("remember_me", False))
    next_url: str | None = extra_data.get("next")
    session_started_at = extra_data.get("session_started_at")
    if not isinstance(session_started_at, int):
        session_started_at = None

    if usuario.precisa_trocar_senha:
        token_pw = arena_auth_service.set_pending_password_change_token(
            usuario,
            jwt_service,
            remember_me=remember_me,
            next_page=next_url,
            session_started_at=session_started_at,
        )
        request.session["pending_pw_change_token"] = token_pw
        await session.commit()
        logger.info("Forced password change after 2FA for user %s", usuario.id)
        return _redirect_to(request, "arena_change_password")

    backup_code_used = validation.method_used == user_2fa_service.Autenticacao2FA.BACKUP
    login_mode = "backup_code" if backup_code_used else "2fa"
    history_result = await arena_auth_service.registrar_login_concluido(
        usuario,
        session,
        ip_address=NetworkService.get_ip_from_request(request),
        user_agent=request.headers.get("User-Agent"),
        mode=login_mode,
        geo_service=request.app.state.geo_service,
    )
    if history_result.status != user_service.UserOperationStatus.SUCCESS:
        flash("We could not complete login right now. Please try again.", FlashCategory.DANGER)
        logger.error("Failed to record completed 2FA login for user %s", usuario.id)
        return _redirect_to(request, "arena_login")

    backup_remaining = 0
    if backup_code_used:
        backup_remaining = validation.remaining_backup_codes or 0
        if backup_remaining == 0:
            flash(
                "You used your last backup code. Generate new backup codes from your profile immediately.",
                FlashCategory.DANGER,
            )
        elif backup_remaining <= 2:
            flash(
                f"Backup code used. Only {backup_remaining} backup code(s) remaining — consider regenerating them.",
                FlashCategory.WARNING,
            )
        else:
            flash(
                f"Backup code used. {backup_remaining} backup code(s) remaining.",
                FlashCategory.INFO,
            )

    _flash_password_age_warning(usuario, flash)

    token_login = _issue_login_token(
        jwt_service=jwt_service,
        usuario=usuario,
        remember_me=remember_me,
        session_started_at=session_started_at,
    )
    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type="auth_success",
        actor_user_id=usuario.id,
        actor_label=usuario.email_normalizado,
        metadata={"action": "login", "method": login_mode, "remember_me": remember_me},
    )
    await session.commit()

    if backup_code_used and not user_security_notification_service.send_backup_code_used_email(
        usuario, request.app.state.email_service, remaining=backup_remaining
    ):
        logger.warning("Backup-code-used notification email failed for user %s", usuario.id)

    redirect_url = post_login_redirect_url(usuario, next_url, request)
    response = RedirectResponse(url=redirect_url, status_code=303)
    _set_login_cookie(response, token=token_login, remember_me=remember_me)
    logger.info("2FA verified for user %s", usuario.id)
    return response
