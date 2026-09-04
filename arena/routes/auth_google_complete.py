#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""The step that finishes a Google-first signup.

Google asserts an identity but neither a date of birth nor consent to Arena's
terms, and the LGPD age gate needs the first while the account gate needs the
second. This step collects both, then applies exactly the age rules
``arena.routes.auth_signup`` applies to an ordinary signup -- under 13 refused,
13-17 held for guardian consent, adult activated -- so the door someone came in
through cannot change the outcome.

The account already exists at this point, created inactive by the callback. Until
this step succeeds it fails ``evaluate_account_access_gates`` everywhere, so an
abandoned completion leaves nothing usable behind -- and nothing stranded either:
the callback resumes an in-flight signup at whichever of the pages here matches its
state (the form, the guardian-waiting page, or the under-13 refusal).
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.models.arena_users import ArenaUser
from arena.routes.auth_common import (
    _html,
    _login_failure_message,
    _redirect_to,
    complete_arena_login,
    enforce_resend_throttle,
    record_email_delivery_event,
)
from arena.routes.auth_google_common import (
    PENDING_CONSENT_UID,
    PENDING_SIGNUP_UID,
    peek_session_value,
    public_base_url,
    record_google_event,
    require_google_client,
)
from arena.services import (
    arena_auth_service,
    google_identity_service,
    user_email_service,
    user_service,
)
from arena.services.consent_timeline import build_consent_timeline
from arena.services.user_service import UserOperationStatus
from shared.age_check import AgeStatus, check_age
from shared.services.network_utils import NetworkService
from shared.services.request_rate_limit import get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/google", tags=["arena-auth"])


def _actor_key(request: Request) -> str:
    """Budget identity of an anonymous requester: the proxy-corrected client IP."""
    return f"ip:{get_client_ip(request) or 'unknown'}"


def _parse_date_of_birth(value: str) -> date | None:
    """Parse an HTML date input value, or None when empty."""
    if not value:
        return None
    return date.fromisoformat(value)


async def _render_form(
    request: Request,
    session: AsyncSession,
    *,
    usuario: ArenaUser,
    date_of_birth: str = "",
    email_responsavel_legal: str = "",
    terms_checked: bool = False,
    status_code: int = 200,
) -> Response:
    """Render the completion form, preserving what the user already typed."""
    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "auth/google_complete.html",
            {
                "date_of_birth": date_of_birth,
                "email_responsavel_legal": email_responsavel_legal,
                "terms_checked": terms_checked,
                "google_email": await _google_email_for(session, usuario),
            },
            status_code=status_code,
        )
    )


async def _google_email_for(session: AsyncSession, usuario: ArenaUser) -> str | None:
    """Return the Google account email linked to this Arena account, if any.

    Read from the identity row rather than carried through the session: the
    completion form is revisited across a full page load, and the identity row
    is the durable record of which Google account authorized this signup.
    """
    identity = await google_identity_service.get_identity_for_user(session, usuario.id)
    return identity.google_email if identity is not None else None


async def _load_pending_user(request: Request, session: AsyncSession) -> ArenaUser | None:
    """Load the account this completion step belongs to, if the marker is valid.

    The marker is read rather than consumed: a failed validation must leave the
    user able to correct the form, and only a successful completion clears it.
    """
    uid = request.session.get(PENDING_SIGNUP_UID)
    if not isinstance(uid, str) or not uid:
        return None
    return await session.get(ArenaUser, uid)


@router.get("/complete", name="arena_google_complete")
async def arena_google_complete(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the form collecting date of birth and terms acceptance."""
    require_google_client(request)
    usuario = await _load_pending_user(request, session)
    if usuario is None:
        flash("Start the Google sign-in again to finish creating your account.", FlashCategory.WARNING)
        return _redirect_to(request, "arena_login")
    return await _render_form(request, session, usuario=usuario)


@router.post("/complete", name="arena_google_complete_submit")
async def arena_google_complete_submit(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    date_of_birth: str = Form(""),
    email_responsavel_legal: str = Form(""),
    terms: str | None = Form(None),
) -> Response:
    """Apply the age gate and finish (or hold) the Google-first signup."""
    require_google_client(request)
    usuario = await _load_pending_user(request, session)
    if usuario is None:
        flash("Start the Google sign-in again to finish creating your account.", FlashCategory.WARNING)
        return _redirect_to(request, "arena_login")

    terms_checked = terms is not None
    guardian_email = email_responsavel_legal.strip()

    if not terms_checked:
        flash("You must accept the Terms of Service and Privacy Policy.", FlashCategory.DANGER)
        return await _render_form(
            request,
            session,
            usuario=usuario,
            date_of_birth=date_of_birth,
            email_responsavel_legal=guardian_email,
        )

    try:
        parsed = _parse_date_of_birth(date_of_birth)
    except ValueError:
        parsed = None
        flash("Enter a valid date of birth.", FlashCategory.DANGER)
        return await _render_form(
            request,
            session,
            usuario=usuario,
            date_of_birth=date_of_birth,
            email_responsavel_legal=guardian_email,
            terms_checked=terms_checked,
        )
    if parsed is None:
        flash("Enter your date of birth.", FlashCategory.DANGER)
        return await _render_form(
            request,
            session,
            usuario=usuario,
            email_responsavel_legal=guardian_email,
            terms_checked=terms_checked,
        )

    age_status = check_age(parsed)

    if age_status == AgeStatus.BLOCKED:
        # The account stays inactive and is not deleted: the row is what stops the
        # same Google account from simply signing up again to get a different answer.
        flash(_login_failure_message(UserOperationStatus.UNDERAGE_BLOCKED), FlashCategory.DANGER)
        usuario.dta_nascimento = parsed
        await record_google_event(
            session,
            request,
            event_type="google_signup_created",
            severity="warning",
            user_id=usuario.id,
            actor_label=usuario.email_normalizado,
            metadata={"outcome": "underage_blocked"},
        )
        await session.commit()
        request.session.pop(PENDING_SIGNUP_UID, None)
        return _redirect_to(request, "arena_google_blocked")

    if age_status == AgeStatus.NEEDS_PARENTAL_CONSENT and not guardian_email:
        flash("Enter a parent or legal guardian email address.", FlashCategory.DANGER)
        return await _render_form(
            request,
            session,
            usuario=usuario,
            date_of_birth=date_of_birth,
            terms_checked=terms_checked,
        )

    # Built from the same canonical lifecycle primitives the password-signup and
    # legacy-account-regularization paths use, rather than a Google-only
    # shortcut: regularizar_data_nascimento owns the date-of-birth/consent-state
    # transition (including the consent_generation bump), aceitar_termos_privacidade
    # owns terms acceptance, and ativar_conta owns activation -- each stamping the
    # timestamp its column exists for, which a hand-rolled assignment here would
    # otherwise skip.
    dob_result = await user_service.regularizar_data_nascimento(usuario.id, parsed, session)
    if dob_result.status != UserOperationStatus.SUCCESS or dob_result.user is None:
        logger.error(
            "Google signup completion could not store the date of birth for user %s: %s",
            usuario.id,
            dob_result.status.name,
        )
        flash("We could not finish creating your account right now. Please try again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")
    usuario = dob_result.user

    # Checked, exactly as POST /auth/accept-terms checks it: an account whose
    # terms acceptance silently failed would be activated below without the
    # consent the account gate requires.
    if not await user_service.aceitar_termos_privacidade(usuario, session):
        logger.error("Google signup completion could not record terms acceptance for user %s", usuario.id)
        flash("We could not finish creating your account right now. Please try again.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    if age_status == AgeStatus.NEEDS_PARENTAL_CONSENT:
        return await _hold_for_guardian_consent(request, session, flash, usuario=usuario, guardian_email=guardian_email)

    await user_service.ativar_conta(usuario, session)

    # This path logs the user straight in, so it owns the login-history row the
    # same way the callback does for a returning user. A brand-new account cannot
    # have 2FA enabled, but the condition mirrors efetuar_login rather than
    # relying on that.
    if not usuario.usa_2fa:
        history = await arena_auth_service.registrar_login_concluido(
            usuario,
            session,
            ip_address=NetworkService.get_ip_from_request(request),
            source_port=NetworkService.get_trusted_source_port_from_request(request),
            user_agent=request.headers.get("User-Agent"),
            mode="google",
            geo_service=request.app.state.geo_service,
        )
        if history.status != UserOperationStatus.SUCCESS:
            logger.error("Failed to record completed Google signup login for user %s", usuario.id)
            await session.rollback()
            flash("We could not complete login right now. Please try again.", FlashCategory.DANGER)
            return _redirect_to(request, "arena_login")

    await session.commit()
    request.session.pop(PENDING_SIGNUP_UID, None)
    logger.info("Google-first signup completed for adult user %s", usuario.id)

    return await complete_arena_login(
        request,
        session,
        flash,
        usuario=usuario,
        jwt_service=request.app.state.jwt_service,
        remember_me=False,
        next_url=None,
        method="google",
    )


async def _hold_for_guardian_consent(
    request: Request,
    session: AsyncSession,
    flash: FlashDep,
    *,
    usuario: ArenaUser,
    guardian_email: str,
) -> Response:
    """Keep a 13-17 account inactive and email the guardian for consent.

    Delegates to ``user_email_service.atualizar_email_responsavel``, the single
    write path that already owns this transition for the password-signup and
    legacy-regularization flows: it validates and normalizes the address (a
    malformed one is refused here, on the form the visitor can still correct,
    rather than being stored and silently stranding the account), clears the
    stale consent timestamp, bumps ``consent_generation``, and generates and
    sends the consent link -- so a Google-first signup cannot drift from the
    same invariants ``POST /auth/update-parental-email`` enforces.

    A refusal deliberately does not roll back: the caller's date-of-birth and
    terms-acceptance writes are still flushed but uncommitted, and rolling back
    would expire ``usuario`` -- turning the very next attribute read into a
    synchronous lazy-load, which the async driver refuses. Returning without a
    commit is enough; ``get_db`` closes the session without one, discarding the
    same pending writes.
    """
    # Read before the service call: a malformed-email refusal still needs this
    # for the re-rendered form, and ``usuario`` must not be touched again after
    # a mid-flight commit below -- see the rollback note above.
    date_of_birth_display = usuario.dta_nascimento.isoformat() if usuario.dta_nascimento else ""

    try:
        result = await user_email_service.atualizar_email_responsavel(
            user_id=usuario.id,
            email_responsavel_legal=guardian_email,
            session=session,
            jwt_service=request.app.state.jwt_service,
            email_service=request.app.state.email_service,
            url_base=public_base_url(request),
            actor_key=_actor_key(request),
        )
    except Exception as exc:  # noqa: BLE001 - delivery must never break the signup
        logger.warning("Parental-consent email send failed for user %s: %s", usuario.id, exc)
        flash(
            "We could not send the consent email right now. Please try again later.",
            FlashCategory.DANGER,
        )
        return await _render_form(
            request,
            session,
            usuario=usuario,
            date_of_birth=date_of_birth_display,
            email_responsavel_legal=guardian_email,
            terms_checked=True,
        )

    if result.status == UserOperationStatus.INVALID_EMAIL:
        flash("Enter a valid parent or legal guardian email address.", FlashCategory.DANGER)
        return await _render_form(
            request,
            session,
            usuario=usuario,
            date_of_birth=date_of_birth_display,
            email_responsavel_legal=guardian_email,
            terms_checked=True,
        )

    sent = result.email_sent
    await record_email_delivery_event(
        session,
        request,
        user_id=usuario.id,
        actor_label=usuario.email_normalizado,
        purpose="parental_consent",
        source="google_signup",
        sent=sent,
    )
    await session.commit()

    # The form is done -- clear its marker -- and the account now waits on a
    # guardian, which the dedicated waiting page owns under its own marker.
    request.session.pop(PENDING_SIGNUP_UID, None)
    request.session[PENDING_CONSENT_UID] = usuario.id

    if not sent:
        flash(
            "Your account was created, but we could not send the consent email. You can ask us to try again below.",
            FlashCategory.WARNING,
        )
    return _redirect_to(request, "arena_google_pending_consent")


@router.get("/blocked", name="arena_google_blocked")
async def arena_google_blocked(request: Request) -> Response:
    """Explain the under-13 refusal.

    Stateless by design: the outcome is a fixed policy, not an account's
    current data, so this reads nothing and can be safely reloaded, bookmarked,
    or reached again on a later visit while the account stays inactive.

    Deliberately **not** gated by ``require_google_client``, unlike every other
    route in this module: an account already refused here must keep being able
    to see why even if the deployment later disables Google sign-in entirely,
    since disabling the feature must not turn an existing blocked visitor's
    bookmark into a dead link.
    """
    templates = request.app.state.arena_templates
    return _html(templates.TemplateResponse(request, "auth/google_blocked.html", {}))


async def _load_pending_consent_user(request: Request, session: AsyncSession) -> ArenaUser | None:
    """Load the account the waiting page belongs to, without consuming the marker.

    The marker must survive a reload and a resend, so it is cleared only once the
    account is resolved one way or the other -- consented, or no longer found.
    """
    uid = peek_session_value(request, PENDING_CONSENT_UID)
    if uid is None:
        return None
    return await session.get(ArenaUser, uid)


@router.get("/pending-consent", name="arena_google_pending_consent")
async def arena_google_pending_consent(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Render the chronology a 13-17 Google signup waits on."""
    require_google_client(request)
    usuario = await _load_pending_consent_user(request, session)
    if usuario is None:
        flash("Start the Google sign-in again to check your account's status.", FlashCategory.WARNING)
        return _redirect_to(request, "arena_login")

    # A guardian may have already granted consent through the emailed link,
    # which is a separate, unrelated flow this page does not own. A GET never
    # authenticates the visitor -- it only points them back to sign in again.
    if usuario.ativo:
        request.session.pop(PENDING_CONSENT_UID, None)
        flash("Your account is ready. Sign in to continue.", FlashCategory.SUCCESS)
        return _redirect_to(request, "arena_login")

    templates = request.app.state.arena_templates
    return _html(
        templates.TemplateResponse(
            request,
            "auth/google_pending_consent.html",
            {
                "guardian_email": usuario.email_responsavel_legal or "",
                "google_email": await _google_email_for(session, usuario),
                "timeline": build_consent_timeline(usuario),
            },
        )
    )


@router.post("/pending-consent/resend", name="arena_google_pending_consent_resend")
async def arena_google_pending_consent_resend(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Re-send the parental consent email for a Google-first signup.

    A distinct throttle action from the password-signup resend, so the two
    unrelated flows cannot exhaust each other's budget.
    """
    require_google_client(request)
    retry_after = await enforce_resend_throttle(request, session, action="resend_google_parental_consent")
    if retry_after is not None:
        flash("Too many requests. Please try again later.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_google_pending_consent")

    usuario = await _load_pending_consent_user(request, session)
    if usuario is None:
        flash("Start the Google sign-in again to check your account's status.", FlashCategory.WARNING)
        return _redirect_to(request, "arena_login")

    try:
        result = await user_email_service.revalidar_consentimento_responsavel(
            user_id=usuario.id,
            session=session,
            jwt_service=request.app.state.jwt_service,
            email_service=request.app.state.email_service,
            url_base=public_base_url(request),
            actor_key=_actor_key(request),
        )
    except Exception as exc:  # noqa: BLE001 - delivery must never break the flow
        logger.warning("Google parental-consent resend failed for user %s: %s", usuario.id, exc)
        await record_email_delivery_event(
            session,
            request,
            user_id=usuario.id,
            purpose="parental_consent",
            source="google_resend",
            sent=False,
        )
        await session.commit()
        flash("We could not send the consent email right now. Please try again later.", FlashCategory.DANGER)
        return _redirect_to(request, "arena_google_pending_consent")

    if result.user is not None and (result.email_sent or result.status == UserOperationStatus.SEND_EMAIL_ERROR):
        await record_email_delivery_event(
            session,
            request,
            user_id=result.user.id,
            actor_label=result.user.email_normalizado,
            purpose="parental_consent",
            source="google_resend",
            sent=result.email_sent,
        )
    await session.commit()

    if result.status == UserOperationStatus.SUCCESS and result.email_sent:
        flash("Consent email sent. Ask your parent or legal guardian to check their inbox.", FlashCategory.SUCCESS)
    elif result.status == UserOperationStatus.SUCCESS:
        # revalidar_consentimento_responsavel treats already-consented as SUCCESS
        # with nothing to send; the next GET will see ativo and route to login.
        flash("Consent is already confirmed. Sign in to continue.", FlashCategory.INFO)
    else:
        flash("We could not send the consent email right now. Please try again later.", FlashCategory.DANGER)
    return _redirect_to(request, "arena_google_pending_consent")
