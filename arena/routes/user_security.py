#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena user security routes — 2FA setup, confirmation, disable, and backup codes."""

import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user
from arena.models.arena_users import ArenaUser
from arena.services import backup2fa_service, user_2fa_service, user_security_notification_service
from arena.services.session_service import build_current_next_url, build_login_redirect_response
from arena.services.user_2fa_service import Autenticacao2FA

logger = logging.getLogger(__name__)

router = APIRouter(tags=["arena-user-security"])


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction.

    Args:
        response: Template response returned by Jinja2.

    Returns:
        HTMLResponse: Response typed for FastAPI handlers.
    """
    return cast(HTMLResponse, response)


def _redirect_to(request: Request, endpoint: str) -> RedirectResponse:
    """Build a 303 redirect to a named route endpoint.

    Args:
        request: Current HTTP request.
        endpoint: Named route endpoint.

    Returns:
        RedirectResponse: 303 redirect to the named endpoint.
    """
    return RedirectResponse(url=str(request.url_for(endpoint)), status_code=303)


@router.get("/user/profile/2fa/setup", response_class=HTMLResponse, name="arena_2fa_setup")
async def arena_2fa_setup(
    request: Request,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the 2FA setup page with a TOTP QR Code and formatted secret.

    Initiates the 2FA activation flow by storing a tentative TOTP secret in the
    database, issuing a short-lived activation token, and rendering the setup
    page with a QR Code for the user's authenticator app.

    Args:
        request: Current HTTP request.
        flash: Flash message dependency.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        Response: 2FA setup page for authenticated users, or a login redirect.
    """
    if current_user is None:
        return build_login_redirect_response(request, next_url=build_current_next_url(request))

    jwt_service = request.app.state.jwt_service
    qrcode_service = request.app.state.qrcode_service

    result = await user_2fa_service.iniciar_ativacao_2fa(current_user, session, jwt_service)

    if result.status == Autenticacao2FA.ALREADY_ENABLED:
        flash("Two-factor authentication is already enabled.", FlashCategory.WARNING)
        return _redirect_to(request, "arena_user_profile")

    await session.commit()
    request.session["activating_2fa_token"] = result.token or ""

    qr_b64 = qrcode_service.generate_totp_qrcode(
        secret=current_user.otp_secret or "",
        user=current_user.email,
        issuer="NOCA Arena",
        as_bytes=False,
    )
    formatted_secret = user_2fa_service.otp_secret_formatado(current_user)

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "auth/2fa_setup.html",
            {
                "qr_code": qr_b64,
                "formatted_secret": formatted_secret,
                "current_user": current_user,
            },
        )
    )


@router.post("/user/profile/2fa/confirm", name="arena_2fa_confirm")
async def arena_2fa_confirm(
    request: Request,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
    full_code: str = Form(""),
) -> Response:
    """Confirm and activate 2FA after the user supplies their first TOTP code.

    Validates the setup session token, verifies the TOTP code, enables 2FA, and
    stores backup codes in the session for one-time display.

    Args:
        request: Current HTTP request.
        flash: Flash message dependency.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.
        full_code: TOTP code submitted by the user.

    Returns:
        Response: Redirect to backup codes page on success, or setup page on failure.
    """
    if current_user is None:
        return build_login_redirect_response(request, next_url=request.url_for("arena_2fa_setup").path)

    jwt_service = request.app.state.jwt_service
    token = request.session.get("activating_2fa_token", "")

    validation = user_2fa_service.validar_token_ativacao_2fa(current_user, token, jwt_service)
    if validation.status not in (Autenticacao2FA.ENABLING,):
        flash("Session expired. Please start the 2FA setup again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_2fa_setup")

    secret = validation.secret or ""
    result = await user_2fa_service.confirmar_ativacao_2fa(
        current_user,
        secret,
        full_code.strip(),
        session,
        gerar_backup_codes=True,
        quantidade_backup=10,
    )

    if result.status != Autenticacao2FA.ENABLED:
        flash("Invalid code. Please try again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_2fa_setup")

    request.session["new_backup_codes"] = result.backup_codes or []
    request.session.pop("activating_2fa_token", None)
    await session.commit()

    if not user_security_notification_service.send_2fa_enabled_email(current_user, request.app.state.email_service):
        logger.warning("2FA-enabled notification email failed for user %s", current_user.id)
    flash("Two-factor authentication has been enabled.", FlashCategory.SUCCESS)
    return _redirect_to(request, "arena_backup_codes")


@router.post("/user/profile/2fa/disable", name="arena_2fa_disable")
async def arena_2fa_disable(
    request: Request,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
    password: str = Form(""),
) -> Response:
    """Disable 2FA for the current user after verifying their password.

    Args:
        request: Current HTTP request.
        flash: Flash message dependency.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.
        password: User's account password for confirmation.

    Returns:
        Response: Redirect to profile page.
    """
    if current_user is None:
        return build_login_redirect_response(request, next_url=request.url_for("arena_user_profile").path)

    if not current_user.check_password(password):
        flash("Incorrect password.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_user_profile")

    if not current_user.usa_2fa:
        flash("2FA is not enabled.", FlashCategory.WARNING)
        return _redirect_to(request, "arena_user_profile")

    await user_2fa_service.desativar_2fa(current_user, session)
    await session.commit()

    if not user_security_notification_service.send_2fa_disabled_self_email(
        current_user, request.app.state.email_service
    ):
        logger.warning("2FA-disabled notification email failed for user %s", current_user.id)
    flash("Two-factor authentication has been disabled.", FlashCategory.SUCCESS)
    return _redirect_to(request, "arena_user_profile")


@router.post("/user/profile/backup-codes/regenerate", name="arena_backup_codes_regenerate")
async def arena_backup_codes_regenerate(
    request: Request,
    flash: FlashDep,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Regenerate backup codes for the current user.

    Invalidates all existing backup codes and generates a fresh set, then stores
    them in the session for one-time display.

    Args:
        request: Current HTTP request.
        flash: Flash message dependency.
        current_user: Authenticated Arena user, or ``None`` for guests.
        session: Active database session.

    Returns:
        Response: Redirect to backup codes page on success, or profile page if 2FA not enabled.
    """
    if current_user is None:
        return build_login_redirect_response(request, next_url=request.url_for("arena_user_profile").path)

    if not current_user.usa_2fa:
        flash("2FA is not enabled.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_user_profile")

    codes = await backup2fa_service.gerar_novos_codigos(current_user, session, quantidade=10)
    await session.commit()

    request.session["new_backup_codes"] = codes
    flash("New backup codes generated. Save them somewhere safe.", FlashCategory.SUCCESS)
    return _redirect_to(request, "arena_backup_codes")


@router.get("/user/profile/backup-codes", response_class=HTMLResponse, name="arena_backup_codes")
async def arena_backup_codes(
    request: Request,
    current_user: ArenaUser | None = Depends(get_current_arena_user),
) -> Response:
    """Render the backup codes page, consuming codes from the session.

    Codes are only shown once: the session key is popped on the first visit.
    If no codes are present in the session the user is redirected to their profile.

    Args:
        request: Current HTTP request.
        current_user: Authenticated Arena user, or ``None`` for guests.

    Returns:
        Response: Backup codes page if codes are present, or a profile redirect.
    """
    if current_user is None:
        return build_login_redirect_response(request, next_url=request.url_for("arena_user_profile").path)

    codes: list[str] | None = request.session.pop("new_backup_codes", None)

    if not codes:
        return _redirect_to(request, "arena_user_profile")

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "users/backup_codes.html",
            {"codes": codes, "current_user": current_user},
        )
    )
