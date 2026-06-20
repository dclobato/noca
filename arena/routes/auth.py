#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena login, logout, and LGPD age/parental-consent gate routes."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.database import get_db
from arena.routes.auth_common import (
    _flash_password_age_warning,
    _issue_login_token,
    _login_failure_message,
    _set_login_cookie,
)
from arena.services import arena_auth_service, user_email_service, user_service
from arena.services.session_service import post_login_redirect_url
from shared.age_check import AgeStatus, check_age
from shared.services.network_utils import NetworkService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["arena-auth"])


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _base_url(request: Request) -> str:
    """Return the public base URL used to build email links."""
    return settings.ARENA_URL_BASE or str(request.base_url).rstrip("/")


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


def _pending_parental_context(usuario: Any) -> dict[str, Any]:
    """Build the login template context for pending parental consent."""
    return {
        "show_resend_parental": bool(usuario.email_responsavel_legal),
        "show_parental_email_form": not bool(usuario.email_responsavel_legal),
        "masked_parental_email": usuario.email_responsavel_legal or "",
    }


@router.get("/login", response_class=HTMLResponse, name="arena_login")
async def arena_login(request: Request, next: str | None = None) -> HTMLResponse:
    """Render the Arena login page."""
    templates = request.app.state.arena_templates
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
    user_agent = request.headers.get("User-Agent")

    result = await arena_auth_service.efetuar_login(
        email=email.strip(),
        password=password,
        session=session,
        ip_address=ip_address,
        user_agent=user_agent,
        geo_service=geo_service,
    )

    if result.status == user_service.UserOperationStatus.USER_INACTIVE:
        if result.user is not None and not result.user.email_confirmado:
            request.session["pending_resend_uid"] = result.user.id
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
        if result.user is not None and _user_needs_parental_consent(result.user):
            request.session["pending_parental_uid"] = result.user.id
            flash(
                "This account is waiting for parent or legal guardian consent.",
                FlashCategory.WARNING,
            )
            return _html(
                templates.TemplateResponse(
                    request,
                    "auth/login.html",
                    _pending_parental_context(result.user),
                    status_code=200,
                )
            )
        flash("Your account has been deactivated. Please contact support.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    if result.status == user_service.UserOperationStatus.PARENTAL_CONSENT_REQUIRED and result.user is not None:
        request.session["pending_parental_uid"] = result.user.id
        flash(_login_failure_message(result.status), FlashCategory.WARNING)
        return _html(
            templates.TemplateResponse(
                request,
                "auth/login.html",
                _pending_parental_context(result.user),
                status_code=200,
            )
        )

    if result.status == user_service.UserOperationStatus.AGE_RECONFIRMATION_REQUIRED and result.user is not None:
        request.session["pending_age_uid"] = result.user.id
        flash(_login_failure_message(result.status), FlashCategory.WARNING)
        return _html(
            templates.TemplateResponse(
                request,
                "auth/login.html",
                {"show_age_reconfirmation": True},
                status_code=200,
            )
        )

    if result.status == user_service.UserOperationStatus.UNDERAGE_BLOCKED:
        flash(_login_failure_message(result.status), FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    if result.status != user_service.UserOperationStatus.SUCCESS or result.user is None:
        flash(_login_failure_message(result.status), FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    usuario = result.user
    await session.commit()

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
            next_page=next,
            session_started_at=session_started_at,
        )
        request.session["pending_2fa_token"] = token_2fa
        logger.info("2FA required for user %s — redirecting to 2FA page", usuario.id)
        return _redirect_to(request, "arena_2fa")

    if usuario.precisa_trocar_senha:
        token_pw = arena_auth_service.set_pending_password_change_token(
            usuario,
            jwt_service,
            remember_me=remember_me,
            next_page=next,
            session_started_at=session_started_at,
        )
        request.session["pending_pw_change_token"] = token_pw
        logger.info("Forced password change for user %s — redirecting to change-password page", usuario.id)
        return _redirect_to(request, "arena_change_password")

    _flash_password_age_warning(usuario, flash)

    token = _issue_login_token(
        jwt_service=jwt_service,
        usuario=usuario,
        remember_me=remember_me,
        session_started_at=session_started_at,
    )

    logger.info("Successful login for user %s from %s", usuario.id, ip_address)
    response = RedirectResponse(url=post_login_redirect_url(usuario, next, request), status_code=303)
    _set_login_cookie(response, token=token, remember_me=remember_me)
    return response


@router.post("/resend-activation", name="arena_resend_activation")
async def arena_resend_activation(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Re-send the email confirmation link for an unconfirmed account."""
    uid: str | None = request.session.pop("pending_resend_uid", None)
    if not uid:
        flash("No pending activation request found. Please log in to try again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    result = await user_email_service.revalidar_email(
        user_id=uid,
        session=session,
        jwt_service=request.app.state.jwt_service,
        email_service=request.app.state.email_service,
        url_base=_base_url(request),
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
    uid: str | None = request.session.get("pending_parental_uid")
    if not uid:
        flash("No pending consent request found. Please log in to try again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    result = await user_email_service.revalidar_consentimento_responsavel(
        user_id=uid,
        session=session,
        jwt_service=request.app.state.jwt_service,
        email_service=request.app.state.email_service,
        url_base=_base_url(request),
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
    uid: str | None = request.session.get("pending_parental_uid")
    if not uid:
        flash("No pending consent request found. Please log in to try again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    result = await user_email_service.atualizar_email_responsavel(
        user_id=uid,
        email_responsavel_legal=email_responsavel_legal.strip(),
        session=session,
        jwt_service=request.app.state.jwt_service,
        email_service=request.app.state.email_service,
        url_base=_base_url(request),
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
    """Regularise a legacy account that does not have a date of birth."""
    uid: str | None = request.session.get("pending_age_uid")
    if not uid:
        flash("No pending age confirmation found. Please log in to try again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

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
) -> Response:
    """Log the current user out of the Arena."""
    jwt_service = request.app.state.jwt_service
    token: str | None = request.cookies.get("arena_access_token")

    if token:
        await arena_auth_service.efetuar_logout(token, jwt_service)
        logger.info("User logged out — token revoked")

    flash("You have been successfully logged out.", FlashCategory.SUCCESS)
    response = _redirect_to(request, "arena_dashboard")
    response.delete_cookie("arena_access_token", httponly=True, samesite="lax")
    return response
