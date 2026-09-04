#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena signup, email confirmation, and terms-acceptance routes.

The parental-consent grant flow lives in :mod:`arena.routes.auth_parental_grant`,
and its revocation counterpart in :mod:`arena.routes.auth_parental_revoke`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.routing import APIRoute
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from arena.config import settings
from arena.database import get_db
from arena.models.arena_users import ArenaUser
from arena.routes.auth_common import (
    AUTH_RATE_LIMITER,
    SIGNUP_RATE_LIMITER,
    SIGNUP_REQUEST_RATE_LIMITER,
    _auth_rate_limit_settings,
    _login_failure_message,
    _signup_rate_limit_policy,
    _signup_request_rate_limit_policy,
    _token_failure_message,
    _validate_password_fields,
    enforce_resend_throttle,
    record_email_delivery_event,
)
from arena.routes.auth_throttle import throttled_response
from arena.routes.auth_token_redeem import (
    build_token_redeem_identity,
    reject_token_redemption,
    reset_token_redeem_throttle,
)
from arena.services import (
    signup_reputation_service,
    user_email_service,
    user_registration_service,
    user_service,
)
from arena.services.session_service import write_flash_message
from arena.services.token_service import ArenaTokenAction
from shared.age_check import AgeStatus, check_age
from shared.services.auth_rate_limit import (
    build_auth_throttle_identity,
    check_auth_throttle,
    record_auth_failure,
)
from shared.services.imageprocessing_service import ImageProcessingError
from shared.services.password_service import PasswordPolicy
from shared.services.request_rate_limit import enforce_ip_rate_limit, get_client_ip
from shared.services.security_events import record_request_security_event

logger = logging.getLogger(__name__)
_SIGNUP_SUCCESS_MESSAGE = "If this account can be created, we will send the next steps by email."


class SignupRequestRateLimitRoute(APIRoute):
    """Run the signup flood guard before FastAPI resolves multipart fields."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """Wrap only the signup submission handler with its pre-body ceiling."""
        original_route_handler = super().get_route_handler()
        guards_signup = self.name == "arena_signup_submit"

        async def rate_limited_route_handler(request: Request) -> Response:
            if guards_signup:
                try:
                    await _enforce_signup_request_rate_limit(request)
                except HTTPException as exc:
                    await _audit_signup_request_lockout(request)
                    write_flash_message(
                        request,
                        "Too many signup attempts from your network. Try again later.",
                        FlashCategory.DANGER,
                    )
                    return _signup_response(
                        request,
                        status_code=exc.status_code,
                        headers=dict(exc.headers or {}),
                    )
            return await original_route_handler(request)

        return rate_limited_route_handler


router = APIRouter(prefix="/auth", tags=["arena-auth"], route_class=SignupRequestRateLimitRoute)


def _html(response: Any) -> HTMLResponse:
    """Cast a TemplateResponse to HTMLResponse for type-checker satisfaction."""
    return cast(HTMLResponse, response)


def _base_url(request: Request) -> str:
    """Return the public base URL used to build email links."""
    return settings.ARENA_URL_BASE or str(request.base_url).rstrip("/")


def _email_actor_key(request: Request) -> str:
    """Budget identity of an anonymous requester: the proxy-corrected client IP."""
    return f"ip:{get_client_ip(request) or 'unknown'}"


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
    if status == user_service.UserOperationStatus.USERNAME_CONFLICT:
        return "We could not assign you a username right now. Please try again."
    return "We could not create your account right now. Please review the form and try again."


async def _run_signup_reputation(
    request: Request,
    *,
    user_id: str,
    email: str,
    ip_address: str | None,
    reputation_enabled: bool,
) -> None:
    """Background entry point: record signup reputation in a fresh DB session."""
    session_factory = request.app.state.arena_db_session
    async with session_factory() as session:
        await signup_reputation_service.record_signup_reputation(
            session,
            user_id=user_id,
            email=email,
            ip_address=ip_address,
            ip_service=request.app.state.ip_reputation_service,
            email_reputation_service=request.app.state.email_reputation_service,
            email_service=request.app.state.email_service,
            reputation_enabled=reputation_enabled,
        )


def _signup_reputation_task(
    request: Request,
    *,
    user_id: str,
    email: str,
) -> BackgroundTask:
    """Build the post-signup reputation background task.

    The signup IP is always recorded so it can be scored later by the backfill
    script; the IPQualityScore lookups and admin notification only run when
    ``NOCA_IPQUALITYSCORE_APIKEY`` is configured.
    """
    ip_address = request.client.host if request.client is not None else None
    return BackgroundTask(
        _run_signup_reputation,
        request,
        user_id=user_id,
        email=email,
        ip_address=ip_address,
        reputation_enabled=bool(settings.IPQUALITYSCORE_APIKEY),
    )


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
    headers: dict[str, str] | None = None,
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
            headers=headers,
        )
    )


@router.get("/signup", response_class=HTMLResponse, name="arena_signup")
async def arena_signup(request: Request) -> HTMLResponse:
    """Render the Arena sign-up page."""
    return _signup_response(request, status_code=200)


async def _enforce_signup_request_rate_limit(request: Request) -> None:
    """Reject signup floods before FastAPI parses the multipart request body."""
    await enforce_ip_rate_limit(
        request,
        policy=_signup_request_rate_limit_policy(),
        fallback_limiter=SIGNUP_REQUEST_RATE_LIMITER,
    )


async def _audit_signup_request_lockout(request: Request) -> None:
    """Persist one rejection from the pre-body signup flood guard."""
    async with request.app.state.arena_db_session() as audit_session:
        await record_request_security_event(
            audit_session,
            request,
            module="arena",
            event_type="auth_throttle_lockout",
            severity="warning",
            metadata={"action": "signup", "reason": "ip_request_window"},
        )
        await audit_session.commit()


@router.post(
    "/signup",
    name="arena_signup_submit",
)
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

    async def _ip_window_response(reason: str, exc: HTTPException) -> Response:
        """Audit one spent per-IP signup window and re-render the form as 429."""
        await record_request_security_event(
            session,
            request,
            module="arena",
            event_type="auth_throttle_lockout",
            severity="warning",
            metadata={"action": "signup", "reason": reason},
        )
        await session.commit()
        flash("Too many signup attempts from your network. Try again later.", FlashCategory.DANGER)
        return _signup_response(
            request,
            full_name=full_name,
            date_of_birth=date_of_birth,
            email=email,
            email_responsavel_legal=email_responsavel_legal,
            terms_checked=terms_checked,
            status_code=exc.status_code,
            headers=dict(exc.headers or {}),
        )

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

    # Everything above is free to check and rejects nothing but a malformed
    # form. From here on the request reaches the account lookup, the paid
    # reputation calls, the insert and the activation email -- which is exactly
    # what the per-IP attempt budget was written to protect.
    try:
        await enforce_ip_rate_limit(request, policy=_signup_rate_limit_policy(), fallback_limiter=SIGNUP_RATE_LIMITER)
    except HTTPException as exc:
        return await _ip_window_response("ip_window", exc)

    throttle_settings = _auth_rate_limit_settings()
    throttle_identity = build_auth_throttle_identity(
        request,
        module="arena",
        action="signup",
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
            metadata={"action": "signup", "reason": throttle_check.reason},
        )
        await session.commit()
        flash("Too many failed attempts. Try again later.", FlashCategory.DANGER)
        return _signup_response(
            request,
            full_name=full_name,
            date_of_birth=date_of_birth,
            email=email,
            email_responsavel_legal=email_responsavel_legal,
            terms_checked=terms_checked,
            status_code=429,
            headers={"Retry-After": str(throttle_check.retry_after_seconds or throttle_settings.lockout_seconds)},
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
    if result.status == user_service.UserOperationStatus.USER_ALREADY_REGISTERED:
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
            event_type="signup_existing_account",
            severity="warning" if failure.locked else "info",
            identifier_hash=throttle_identity.identifier_hash,
            metadata={"action": "signup", "reason": failure.reason},
        )
        await session.commit()
        await user_registration_service.enviar_email_conta_existente(
            email,
            request.app.state.email_service,
            _base_url(request),
            actor_key=_email_actor_key(request),
        )
        flash(_SIGNUP_SUCCESS_MESSAGE, FlashCategory.SUCCESS)
        return _redirect_to(request, "arena_login")
    if result.status != user_service.UserOperationStatus.SUCCESS or result.user is None:
        flash(_signup_failure_message(result.status), FlashCategory.DANGER)
        # A username clash is a conflict, not a malformed submission: the form
        # was fine and resubmitting it unchanged is the correct response, so it
        # must not be reported as the 422 every other failure uses.
        conflict = result.status == user_service.UserOperationStatus.USERNAME_CONFLICT
        return _signup_response(
            request,
            full_name=full_name,
            date_of_birth=date_of_birth,
            email=email,
            email_responsavel_legal=email_responsavel_legal,
            terms_checked=terms_checked,
            status_code=409 if conflict else 422,
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

    await record_request_security_event(
        session,
        request,
        module="arena",
        event_type="account_signup_created",
        actor_user_id=result.user.id,
        actor_label=result.user.email_normalizado,
        metadata={
            "parental_consent_required": age_status == AgeStatus.NEEDS_PARENTAL_CONSENT,
            "profile_photo_uploaded": upload is not None and bool(upload.filename),
        },
    )
    await session.commit()
    try:
        email_sent = await user_registration_service.enviar_email_ativacao(
            result.user,
            result.token or "",
            request.app.state.email_service,
            _base_url(request),
            actor_key=_email_actor_key(request),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Activation email send failed for user %s: %s", result.user.id, exc)
        email_sent = False
    await record_email_delivery_event(
        session,
        request,
        user_id=result.user.id,
        actor_label=result.user.email_normalizado,
        purpose="account_activation",
        source="signup",
        sent=email_sent,
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
        try:
            parental_email_sent = await user_registration_service.enviar_email_consentimento_responsavel(
                result.user,
                parental_token,
                request.app.state.email_service,
                _base_url(request),
                actor_key=_email_actor_key(request),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Parental-consent email send failed for user %s: %s", result.user.id, exc)
            parental_email_sent = False
        await record_email_delivery_event(
            session,
            request,
            user_id=result.user.id,
            actor_label=result.user.email_normalizado,
            purpose="parental_consent",
            source="signup",
            sent=parental_email_sent,
        )
    await session.commit()
    reputation_task = _signup_reputation_task(request, user_id=result.user.id, email=email)
    if email_sent and parental_email_sent:
        flash(_SIGNUP_SUCCESS_MESSAGE, FlashCategory.SUCCESS)
    else:
        flash(_SIGNUP_SUCCESS_MESSAGE, FlashCategory.WARNING)
    response = _redirect_to(request, "arena_login")
    response.background = reputation_task
    return response


@router.get("/activate", name="arena_activate")
async def arena_activate(
    request: Request,
    flash: FlashDep,
    token: str = "",
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Activate an account from an email-confirmation JWT.

    This ``GET`` redeems the token, so unlike the password-reset link there is
    no ``POST`` to move the verdict to; it is throttled instead. Only a
    rejected token is counted, so clicking a working link again -- or a mail
    client prefetching it -- costs nothing.
    """
    if not token:
        flash("Activation link is missing a token.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    identity = build_token_redeem_identity(request, token)
    jwt_service = request.app.state.jwt_service
    claims = jwt_service.validar(token)
    if not claims.valid or claims.action != ArenaTokenAction.VALIDATE_EMAIL:
        status = (
            user_service.UserOperationStatus.TOKEN_EXPIRED
            if getattr(claims, "reason", None) == "expired"
            else user_service.UserOperationStatus.INVALID_TOKEN
        )
        return await reject_token_redemption(request, session, flash, identity, status)

    result = await user_email_service.validar_email_por_token(token, session, jwt_service)
    if result.status == user_service.UserOperationStatus.SUCCESS and result.user is not None:
        activated = await user_service.ativar_conta_se_pronta(result.user, session)
        await record_request_security_event(
            session,
            request,
            module="arena",
            event_type="email_confirmed",
            actor_user_id=result.user.id,
            actor_label=result.user.email_normalizado,
            metadata={"source": "activation_token"},
        )
        if activated:
            await record_request_security_event(
                session,
                request,
                module="arena",
                event_type="account_activated",
                actor_user_id=result.user.id,
                actor_label=result.user.email_normalizado,
                metadata={"source": "email_confirmation"},
            )
        await session.commit()
        await reset_token_redeem_throttle(request, identity)
        if activated:
            flash("Your account is active. You can log in now.", FlashCategory.SUCCESS)
        else:
            flash("Email confirmed. The account is waiting for parent or legal guardian consent.", FlashCategory.INFO)
        return _redirect_to(request, "arena_login")

    # A second click on a link that already worked: not a failed redemption.
    if result.status == user_service.UserOperationStatus.EMAIL_ALREADY_CONFIRMED and result.user is not None:
        await user_service.ativar_conta_se_pronta(result.user, session)
        await session.commit()
        await reset_token_redeem_throttle(request, identity)
        flash(_token_failure_message(result.status), FlashCategory.INFO)
        return _redirect_to(request, "arena_login")

    return await reject_token_redemption(request, session, flash, identity, result.status)


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
    """Process the ToS/PP acceptance form.

    A pending-session write with no secret to verify, so it carries only the
    per-IP attempt cap; the check runs after the pending-uid gate so a request
    with no pending flow never consumes the window.
    """
    templates = request.app.state.arena_templates
    uid: str | None = request.session.get("pending_tos_uid")
    if not uid:
        flash("Session expired. Please log in again.", FlashCategory.WARNING)
        return _redirect_to(request, "arena_login")

    retry_after = await enforce_resend_throttle(request, session, action="accept_terms")
    if retry_after is not None:
        return throttled_response(request, flash, retry_after, back_route="arena_login")

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
