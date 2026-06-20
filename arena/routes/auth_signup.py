#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena signup, email confirmation, parental consent, and terms-acceptance routes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.database import get_db
from arena.models.arena_users import ArenaUser
from arena.routes.auth_common import (
    _login_failure_message,
    _token_failure_message,
    _validate_password_fields,
)
from arena.services import user_email_service, user_registration_service, user_service
from arena.services.token_service import ArenaTokenAction
from shared.age_check import AgeStatus, check_age
from shared.services.imageprocessing_service import ImageProcessingError
from shared.services.password_service import PasswordPolicy

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


def _signup_failure_message(status: user_service.UserOperationStatus) -> str:
    """Map signup service statuses to user-facing messages."""
    if status == user_service.UserOperationStatus.USER_ALREADY_REGISTERED:
        return "This email address is already registered."
    if status == user_service.UserOperationStatus.INVALID_EMAIL:
        return "Enter a valid email address."
    if status == user_service.UserOperationStatus.PARENTAL_CONSENT_REQUIRED:
        return "Parent or legal guardian consent is required before you can log in."
    if status == user_service.UserOperationStatus.AGE_RECONFIRMATION_REQUIRED:
        return "Confirm your date of birth before logging in."
    if status == user_service.UserOperationStatus.UNDERAGE_BLOCKED:
        return "The platform is not intended for users under 13 years old."
    if status == user_service.UserOperationStatus.SEND_EMAIL_ERROR:
        return "Your account was created, but the confirmation email could not be sent."
    if status == user_service.UserOperationStatus.DATABASE_ERROR:
        return "We could not create your account right now. Please try again."
    return "We could not create your account right now. Please review the form and try again."


def _signup_context(
    *,
    full_name: str = "",
    date_of_birth: str = "",
    email: str = "",
    email_responsavel_legal: str = "",
    terms_checked: bool = False,
) -> dict[str, Any]:
    """Build context for the signup form without preserving sensitive fields."""
    return {
        "password_hint": PasswordPolicy(settings).policy_hint,
        "signup_form": {
            "full_name": full_name,
            "date_of_birth": date_of_birth,
            "email": email,
            "email_responsavel_legal": email_responsavel_legal,
            "terms_checked": terms_checked,
        },
    }


def _signup_response(
    request: Request,
    *,
    full_name: str = "",
    date_of_birth: str = "",
    email: str = "",
    email_responsavel_legal: str = "",
    terms_checked: bool = False,
    status_code: int = 422,
) -> HTMLResponse:
    """Render signup with submitted safe fields preserved."""
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "auth/signup.html",
            _signup_context(
                full_name=full_name,
                date_of_birth=date_of_birth,
                email=email,
                email_responsavel_legal=email_responsavel_legal,
                terms_checked=terms_checked,
            ),
            status_code=status_code,
        )
    )


@router.get("/signup", response_class=HTMLResponse, name="arena_signup")
async def arena_signup(request: Request) -> HTMLResponse:
    """Render the Arena sign-up page."""
    return _signup_response(request, status_code=200)


@router.post("/signup", name="arena_signup_submit")
async def arena_signup_submit(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    full_name: str = Form(""),
    date_of_birth: str = Form(""),
    email: str = Form(""),
    email_responsavel_legal: str = Form(""),
    password: str = Form(""),
    confirm_password: str = Form(""),
    terms: str | None = Form(None),
    profile_photo: UploadFile | None = File(None),
    foto_cropada: UploadFile | None = File(None),
) -> Response:
    """Create an Arena account and send the email-confirmation link."""
    full_name = full_name.strip()
    email = email.strip()
    email_responsavel_legal = email_responsavel_legal.strip()
    terms_checked = terms is not None
    if not full_name:
        flash("Enter your full name.", FlashCategory.DANGER)
        return _signup_response(
            request,
            full_name=full_name,
            date_of_birth=date_of_birth,
            email=email,
            email_responsavel_legal=email_responsavel_legal,
            terms_checked=terms_checked,
        )
    if not terms_checked:
        flash("You must accept the terms before creating an account.", FlashCategory.DANGER)
        return _signup_response(
            request,
            full_name=full_name,
            date_of_birth=date_of_birth,
            email=email,
            email_responsavel_legal=email_responsavel_legal,
            terms_checked=terms_checked,
        )

    password_error = _validate_password_fields(password, confirm_password)
    if password_error is not None:
        flash(password_error, FlashCategory.DANGER)
        return _signup_response(
            request,
            full_name=full_name,
            date_of_birth=date_of_birth,
            email=email,
            email_responsavel_legal=email_responsavel_legal,
            terms_checked=terms_checked,
        )

    try:
        parsed_date_of_birth = _parse_date_of_birth(date_of_birth)
    except ValueError:
        flash("Enter a valid date of birth.", FlashCategory.DANGER)
        return _signup_response(
            request,
            full_name=full_name,
            date_of_birth=date_of_birth,
            email=email,
            email_responsavel_legal=email_responsavel_legal,
            terms_checked=terms_checked,
        )
    if parsed_date_of_birth is None:
        flash("Enter your date of birth.", FlashCategory.DANGER)
        return _signup_response(
            request,
            full_name=full_name,
            date_of_birth=date_of_birth,
            email=email,
            email_responsavel_legal=email_responsavel_legal,
            terms_checked=terms_checked,
        )

    age_status = check_age(parsed_date_of_birth)
    if age_status == AgeStatus.BLOCKED:
        flash(_login_failure_message(user_service.UserOperationStatus.UNDERAGE_BLOCKED), FlashCategory.DANGER)
        return _signup_response(
            request,
            full_name=full_name,
            date_of_birth=date_of_birth,
            email=email,
            email_responsavel_legal=email_responsavel_legal,
            terms_checked=terms_checked,
        )
    if age_status == AgeStatus.NEEDS_PARENTAL_CONSENT and not email_responsavel_legal:
        flash("Enter a parent or legal guardian email address.", FlashCategory.DANGER)
        return _signup_response(
            request,
            full_name=full_name,
            date_of_birth=date_of_birth,
            email=email,
            email_responsavel_legal=email_responsavel_legal,
            terms_checked=terms_checked,
        )

    result = await user_registration_service.registrar_usuario(
        nome=full_name,
        email=email,
        password=password,
        session=session,
        jwt_service=request.app.state.jwt_service,
        email_service=request.app.state.email_service,
        url_base=_base_url(request),
        ativo=False,
        enviar_email=False,
        dta_nascimento=parsed_date_of_birth,
        email_responsavel_legal=(email_responsavel_legal if age_status == AgeStatus.NEEDS_PARENTAL_CONSENT else None),
        consentimento_responsavel=age_status == AgeStatus.ALLOWED,
        aceitou_termos_privacidade=True,
        dta_aceitacao_termos_privacidade=datetime.now(UTC),
    )
    if result.status != user_service.UserOperationStatus.SUCCESS or result.user is None:
        flash(_signup_failure_message(result.status), FlashCategory.DANGER)
        return _signup_response(
            request,
            full_name=full_name,
            date_of_birth=date_of_birth,
            email=email,
            email_responsavel_legal=email_responsavel_legal,
            terms_checked=terms_checked,
        )

    upload = foto_cropada if (foto_cropada is not None and foto_cropada.filename) else profile_photo
    if upload is not None and upload.filename:
        image_service = request.app.state.image_service
        try:
            processed = await image_service.process_upload_image(
                upload=upload,
                crop_aspect_ratio=True,
                aspect_width=2,
                aspect_height=3,
            )
            result.user.apply_processed_photo(
                foto_base64=processed.imagem_base64,
                avatar_base64=processed.avatar_base64,
                mime_type=processed.mime_type,
            )
        except (ImageProcessingError, ValueError) as exc:
            await session.rollback()
            flash(str(exc), FlashCategory.DANGER)
            return _signup_response(
                request,
                full_name=full_name,
                date_of_birth=date_of_birth,
                email=email,
                email_responsavel_legal=email_responsavel_legal,
                terms_checked=terms_checked,
            )

    await session.commit()
    email_sent = user_registration_service.enviar_email_ativacao(
        result.user,
        result.token or "",
        request.app.state.email_service,
        _base_url(request),
    )
    parental_email_sent = True
    if age_status == AgeStatus.NEEDS_PARENTAL_CONSENT:
        parental_token = str(
            request.app.state.jwt_service.criar(
                action=ArenaTokenAction.PARENTAL_CONSENT,
                sub=result.user.id,
                expires_in=86_400,
            )
        )
        parental_email_sent = user_registration_service.enviar_email_consentimento_responsavel(
            result.user,
            parental_token,
            request.app.state.email_service,
            _base_url(request),
        )
    if email_sent and parental_email_sent and age_status == AgeStatus.NEEDS_PARENTAL_CONSENT:
        flash(
            "Account created. Check your email to activate it and ask your parent "
            "or legal guardian to confirm consent.",
            FlashCategory.SUCCESS,
        )
    elif email_sent and not parental_email_sent and age_status == AgeStatus.NEEDS_PARENTAL_CONSENT:
        flash(
            "Account created, but the consent email could not be sent. Log in after email confirmation to resend it.",
            FlashCategory.WARNING,
        )
    elif email_sent:
        flash("Account created. Check your email to activate your account.", FlashCategory.SUCCESS)
    else:
        flash(
            "Account created, but the confirmation email could not be sent. Contact support to activate it.",
            FlashCategory.WARNING,
        )
    return _redirect_to(request, "arena_login")


@router.get("/activate", name="arena_activate")
async def arena_activate(
    request: Request,
    flash: FlashDep,
    token: str = "",
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Activate an account from an email-confirmation JWT."""
    if not token:
        flash("Activation link is missing a token.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    jwt_service = request.app.state.jwt_service
    claims = jwt_service.validar(token)
    if not claims.valid or claims.action != ArenaTokenAction.VALIDATE_EMAIL:
        status = (
            user_service.UserOperationStatus.TOKEN_EXPIRED
            if getattr(claims, "reason", None) == "expired"
            else user_service.UserOperationStatus.INVALID_TOKEN
        )
        flash(_token_failure_message(status), FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    result = await user_email_service.validar_email_por_token(token, session, jwt_service)
    if result.status == user_service.UserOperationStatus.SUCCESS and result.user is not None:
        activated = await user_service.ativar_conta_se_pronta(result.user, session)
        await session.commit()
        if activated:
            flash("Your account is active. You can log in now.", FlashCategory.SUCCESS)
        else:
            flash("Email confirmed. The account is waiting for parent or legal guardian consent.", FlashCategory.INFO)
        return _redirect_to(request, "arena_login")

    if result.status == user_service.UserOperationStatus.EMAIL_ALREADY_CONFIRMED and result.user is not None:
        await user_service.ativar_conta_se_pronta(result.user, session)
        await session.commit()
        flash(_token_failure_message(result.status), FlashCategory.INFO)
        return _redirect_to(request, "arena_login")

    flash(_token_failure_message(result.status), FlashCategory.DANGER)
    return _redirect_to(request, "arena_login")


@router.get("/parental-consent", name="arena_parental_consent")
async def arena_parental_consent(
    request: Request,
    flash: FlashDep,
    token: str = "",
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Confirm parent/legal guardian consent from a consent JWT."""
    if not token:
        flash("Consent link is missing a token.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    jwt_service = request.app.state.jwt_service
    result = await user_email_service.validar_consentimento_responsavel_por_token(token, session, jwt_service)
    if result.status == user_service.UserOperationStatus.SUCCESS and result.user is not None:
        activated = await user_service.ativar_conta_se_pronta(result.user, session)
        await session.commit()
        if activated:
            flash("Consent confirmed. The account is active and ready to use.", FlashCategory.SUCCESS)
        else:
            flash("Consent confirmed. The account is waiting for email confirmation.", FlashCategory.SUCCESS)
        return _redirect_to(request, "arena_login")

    flash(_token_failure_message(result.status), FlashCategory.DANGER)
    return _redirect_to(request, "arena_login")


@router.get("/accept-terms", response_class=HTMLResponse, name="arena_accept_terms")
async def arena_accept_terms(request: Request) -> Response:
    """Render the ToS/PP acceptance page for users who have not accepted yet."""
    if "pending_tos_uid" not in request.session:
        return _redirect_to(request, "arena_login")
    templates = request.app.state.arena_templates
    return _html(templates.TemplateResponse(request, "auth/accept_terms.html", {}))


@router.post("/accept-terms", name="arena_accept_terms_submit")
async def arena_accept_terms_submit(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    terms: str | None = Form(None),
) -> Response:
    """Process the ToS/PP acceptance form."""
    templates = request.app.state.arena_templates
    uid: str | None = request.session.get("pending_tos_uid")
    if not uid:
        flash("Session expired. Please log in again.", FlashCategory.WARNING)
        return _redirect_to(request, "arena_login")

    if terms is None:
        flash(
            "You must accept the Terms of Service and Privacy Policy to continue.",
            FlashCategory.DANGER,
        )
        return _html(templates.TemplateResponse(request, "auth/accept_terms.html", {}, status_code=200))

    usuario = await session.get(ArenaUser, uid)
    if usuario is None:
        request.session.pop("pending_tos_uid", None)
        flash("Account not found. Please try again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    success = await user_service.aceitar_termos_privacidade(usuario, session)
    if not success:
        flash("An error occurred. Please try again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    request.session.pop("pending_tos_uid", None)
    await session.commit()
    flash(
        "Thank you! You have accepted the Terms of Service and Privacy Policy. Please log in again.",
        FlashCategory.SUCCESS,
    )
    return _redirect_to(request, "arena_login")
