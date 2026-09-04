#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena Google sign-in: start, callback, link and unlink.

Google is an *alternative* door, not a replacement one. Once the callback has a
verified subject it hands off to ``complete_arena_login``, the same helper the
password form uses, and it runs ``evaluate_account_access_gates`` first -- so the
terms gate, the 2FA gate and the LGPD age gate apply identically whichever door
was used. Reimplementing that chain here is exactly how one of those gates would
go missing.

The account-creation half of a Google-first signup lives in
``arena.services.google_signup_service``; the step that collects date of birth
and terms lives in ``arena.routes.auth_google_complete``.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi_flash import FlashCategory, FlashDep
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from arena.database import get_db
from arena.dependencies.auth import get_current_arena_user, require_arena_user
from arena.models.arena_users import ArenaUser
from arena.routes.auth_common import (
    AUTH_RATE_LIMITER,
    _auth_rate_limit_settings,
    _redirect_to,
    complete_arena_login,
    render_login_gate_failure,
)
from arena.routes.auth_google_common import (
    GENERIC_FAILURE_MESSAGE,
    PENDING_CONSENT_UID,
    PENDING_LINK_UID,
    PENDING_NEXT_URL,
    PENDING_SIGNUP_UID,
    callback_redirect_uri,
    clear_existing_account_marker,
    public_base_url,
    record_google_event,
    require_google_client,
    take_session_value,
)
from arena.routes.auth_signup import _signup_reputation_task
from arena.routes.auth_throttle import throttled_response
from arena.services import (
    arena_auth_service,
    google_identity_service,
    google_oauth_service,
    google_signup_service,
    user_security_notification_service,
)
from arena.services.google_avatar_service import GoogleAvatarError, GoogleAvatarService
from arena.services.google_oauth_service import GoogleIdentityClaims
from arena.services.user_service import UserOperationStatus
from shared.services.auth_rate_limit import (
    AuthThrottleIdentity,
    build_auth_throttle_identity,
    check_auth_throttle,
    record_auth_failure,
    reset_auth_throttle,
)
from shared.services.network_utils import NetworkService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/google", tags=["arena-auth"])

_THROTTLE_ACTION = "google-login"


def _linked_accounts_redirect(request: Request) -> RedirectResponse:
    """Return to the profile tab that owns Google account controls."""
    url = request.url_for("arena_user_profile").include_query_params(tab="linked-accounts")
    return RedirectResponse(url=str(url), status_code=303)


@router.get("/login", name="arena_google_login")
async def arena_google_login(request: Request, next: str | None = None) -> Response:
    """Start the Google sign-in flow.

    Authlib stores ``state``, ``nonce`` and the PKCE verifier in the Starlette
    session; the caller's post-login target is stored beside them rather than
    round-tripped through Google, so it cannot be tampered with in transit. It is
    still re-validated by ``safe_next_url`` when the login completes.

    A stale ``PENDING_LINK_UID`` from an abandoned link attempt is cleared here
    too: this is an ordinary login, not a link, and a marker left behind by an
    earlier ``/link`` call must not be able to turn this callback into one. The
    "fold into an existing account" marker goes for the same reason -- starting
    Google sign-in again is an explicit statement of a different intent.
    """
    client = require_google_client(request)
    request.session.pop(PENDING_LINK_UID, None)
    clear_existing_account_marker(request)
    request.session[PENDING_NEXT_URL] = next or ""
    redirect = await client.authorize_redirect(request, callback_redirect_uri(request))
    return cast(Response, redirect)


@router.post("/link", name="arena_google_link")
async def arena_google_link(
    request: Request,
    current_user: ArenaUser = Depends(require_arena_user),
) -> Response:
    """Start the flow that links a Google account to the current Arena account.

    Linking is only ever started from an authenticated session, which is what
    makes "the email need not match" safe: the user is telling us which Arena
    account to attach the Google account they are about to authorize to.
    """
    client = require_google_client(request)
    request.session[PENDING_LINK_UID] = str(current_user.id)
    redirect = await client.authorize_redirect(request, callback_redirect_uri(request))
    return cast(Response, redirect)


@router.post("/unlink", name="arena_google_unlink")
async def arena_google_unlink(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    current_user: ArenaUser = Depends(require_arena_user),
) -> Response:
    """Detach the Google account, unless doing so would lock the user out."""
    require_google_client(request)

    identity = await google_identity_service.get_identity_for_user(session, current_user.id)
    if identity is None:
        flash("No Google account is linked to your profile.", FlashCategory.WARNING)
        return _linked_accounts_redirect(request)

    # The last-method guard. An account created through Google holds an unusable
    # placeholder password, so unlinking would leave it with no way in at all.
    # Setting a password through the ordinary reset flow clears the placeholder
    # and makes this permitted.
    if not current_user.has_usable_password:
        flash(
            "Set a password first — Google is currently the only way to sign in to this account. "
            "Use “Forgot password?” on the login page to set one.",
            FlashCategory.DANGER,
        )
        return _linked_accounts_redirect(request)

    if identity.use_google_avatar:
        current_user.bump_avatar_revision()
    await google_identity_service.unlink_identity(session, identity)
    await record_google_event(
        session,
        request,
        event_type="google_account_unlinked",
        severity="warning",
        user_id=current_user.id,
        actor_label=current_user.email_normalizado,
    )
    await session.commit()

    if not await user_security_notification_service.send_google_unlinked_email(
        current_user, request.app.state.email_service
    ):
        logger.warning("Google-unlinked notification email failed for user %s", current_user.id)

    flash("Your Google account has been unlinked.", FlashCategory.SUCCESS)
    return _linked_accounts_redirect(request)


@router.post("/avatar-source", name="arena_google_avatar_source")
async def arena_google_avatar_source(
    request: Request,
    flash: FlashDep,
    source: Annotated[str, Form()],
    session: AsyncSession = Depends(get_db),
    current_user: ArenaUser = Depends(require_arena_user),
) -> Response:
    """Select the linked Google picture or Arena's local avatar source.

    Args:
        request: Current HTTP request.
        flash: Flash message dependency.
        source: Requested source, either ``google`` or ``arena``.
        session: Active async database session.
        current_user: Authenticated Arena user.

    Returns:
        Response: Redirect to the profile's linked-accounts settings.
    """
    require_google_client(request)
    identity = await google_identity_service.get_identity_for_user(session, current_user.id)
    if identity is None:
        flash("Link a Google account before selecting its profile picture.", FlashCategory.WARNING)
        return _linked_accounts_redirect(request)

    if source == "arena":
        changed = await google_identity_service.set_google_avatar_selected(
            session,
            identity=identity,
            user=current_user,
            selected=False,
        )
        await session.commit()
        message = "Arena is now your avatar source." if changed else "Arena is already your avatar source."
        flash(message, FlashCategory.SUCCESS if changed else FlashCategory.INFO)
        return _linked_accounts_redirect(request)

    if source != "google":
        flash("Choose either Arena or Google as your avatar source.", FlashCategory.DANGER)
        return _linked_accounts_redirect(request)

    if not identity.google_picture_url:
        flash("Google did not provide a profile picture for this account.", FlashCategory.WARNING)
        return _linked_accounts_redirect(request)

    avatar_service: GoogleAvatarService = request.app.state.google_avatar_service
    try:
        await google_identity_service.refresh_google_avatar(
            session,
            identity=identity,
            user=current_user,
            avatar_service=avatar_service,
        )
        changed = await google_identity_service.set_google_avatar_selected(
            session,
            identity=identity,
            user=current_user,
            selected=True,
        )
    except (GoogleAvatarError, ValueError) as exc:
        logger.warning("Google avatar selection failed for user %s: %s", current_user.id, exc)
        await session.rollback()
        flash(
            "Google's profile picture could not be imported. Arena kept your current avatar.",
            FlashCategory.WARNING,
        )
        return _linked_accounts_redirect(request)

    await session.commit()
    message = "Google is now your avatar source." if changed else "Your Google avatar was refreshed."
    flash(message, FlashCategory.SUCCESS)
    return _linked_accounts_redirect(request)


@router.get("/callback", name="arena_google_callback")
async def arena_google_callback(
    request: Request,
    flash: FlashDep,
    session: AsyncSession = Depends(get_db),
    current_user: ArenaUser | None = Depends(get_current_arena_user),
) -> Response:
    """Complete a Google sign-in or a Google account link.

    The two intents are told apart by the ``pending_google_link_uid`` marker.
    It is consumed **after** throttle admission and a successful authorization,
    immediately before dispatch -- never on entry. The marker belongs to one
    OAuth round trip, and that round trip is only spent once Authlib has
    redeemed its ``state``: a callback refused with ``429`` never reached
    Authlib, so its state and code are still valid and the user will retry
    them, and a forged callback that fails the state check redeems nothing at
    all. Consuming the marker on either would turn the genuine retry into an
    ordinary login and, for an unknown Google subject, into a stray second
    account -- for a logged-out *and* a still-authenticated linker alike, since
    the current-user check below only runs once the callback knows it is a
    link. Consumed at dispatch, the marker lives exactly as long as the
    transaction it was set for.

    The marker alone is not trusted for a link, though: it lives in the
    Starlette session, which outlives logout (only the JWT cookie is cleared
    there), so a session that started a link and then logged out -- or never
    was the account it names -- must not be able to link a Google account to
    that stale identity. ``current_user``, resolved the same way every other
    authenticated page resolves it, must match.
    """
    client = require_google_client(request)

    throttle_settings = _auth_rate_limit_settings()
    throttle_identity = build_auth_throttle_identity(
        request,
        module="arena",
        action=_THROTTLE_ACTION,
        identifier=None,
        settings=throttle_settings,
    )
    throttle_check = await check_auth_throttle(
        request,
        throttle_identity,
        settings=throttle_settings,
        fallback_limiter=AUTH_RATE_LIMITER,
    )
    if not throttle_check.allowed:
        await record_google_event(
            session,
            request,
            event_type="auth_throttle_lockout",
            severity="warning",
            metadata={"action": _THROTTLE_ACTION, "reason": throttle_check.reason},
        )
        await session.commit()
        return throttled_response(
            request,
            flash,
            throttle_check.retry_after_seconds or throttle_settings.lockout_seconds,
            back_route="arena_login",
        )

    claims = await _authorize(request, session, client, throttle_identity)
    if claims is None:
        flash(GENERIC_FAILURE_MESSAGE, FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    await reset_auth_throttle(request, throttle_identity, fallback_limiter=AUTH_RATE_LIMITER)

    # Consumed here and only here: the OAuth transaction these markers were set
    # for has just been redeemed, so the intent they carry applies to this
    # request and can apply to no other.
    link_uid = take_session_value(request, PENDING_LINK_UID)
    next_url = take_session_value(request, PENDING_NEXT_URL)

    if link_uid is not None:
        return await _complete_link(
            request, session, flash, link_uid=link_uid, claims=claims, current_user=current_user
        )
    return await _complete_login(request, session, flash, claims=claims, next_url=next_url)


async def _authorize(
    request: Request,
    session: AsyncSession,
    client: Any,
    throttle_identity: AuthThrottleIdentity,
) -> GoogleIdentityClaims | None:
    """Exchange the code and return verified claims, or None on any refusal.

    Every failure returns the same ``None`` and records the same event: the
    callback is anonymous, so distinguishing a forged state from an unverified
    address would tell an attacker which half of the flow they had reached.
    """
    try:
        token = await client.authorize_access_token(request)
    except Exception as exc:  # noqa: BLE001 - Authlib raises several unrelated types
        logger.warning("Google authorization failed: %s", exc)
        await _record_failure(request, session, throttle_identity, reason="authorization_failed")
        return None

    claims = google_oauth_service.extract_claims(token)
    if claims is None:
        await _record_failure(request, session, throttle_identity, reason="missing_claims")
        return None

    # An unverified address must never be trusted: Google will assert an address
    # it has not verified, and Arena treats a matching address as identity.
    if not claims.email_verified or not claims.email:
        logger.warning("Google sign-in refused: the account's email address is not verified")
        await _record_failure(request, session, throttle_identity, reason="email_not_verified")
        return None

    return claims


async def _record_failure(
    request: Request,
    session: AsyncSession,
    throttle_identity: AuthThrottleIdentity,
    *,
    reason: str,
) -> None:
    """Record a failed Google sign-in and count it toward the lockout."""
    await record_auth_failure(
        request,
        throttle_identity,
        settings=_auth_rate_limit_settings(),
        fallback_limiter=AUTH_RATE_LIMITER,
    )
    await record_google_event(
        session,
        request,
        event_type="google_login_failure",
        severity="warning",
        metadata={"reason": reason},
    )
    await session.commit()


async def _complete_link(
    request: Request,
    session: AsyncSession,
    flash: FlashDep,
    *,
    link_uid: str,
    claims: GoogleIdentityClaims,
    current_user: ArenaUser | None,
) -> Response:
    """Attach the authorized Google account to the Arena account that asked.

    ``link_uid`` alone is never enough: it is a session marker that can outlive
    the session it was set for (logout clears the JWT cookie, not the
    Starlette session), so the request's *current* authenticated user must be
    the very account that started the link. A mismatch -- logged out, switched
    accounts, or a marker that never belonged to this browser -- is refused
    exactly like any other failed callback, with no hint as to which half
    failed.
    """
    if current_user is None or current_user.id != link_uid:
        logger.warning("Google link refused: no authenticated session matches the pending link intent")
        await record_google_event(
            session,
            request,
            event_type="google_link_conflict",
            severity="warning",
            metadata={"reason": "stale_link_session"},
        )
        await session.commit()
        flash(GENERIC_FAILURE_MESSAGE, FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    usuario = current_user

    try:
        async with session.begin_nested():
            await google_identity_service.link_identity(session, user_id=usuario.id, claims=claims)
    except IntegrityError:
        # Either this Arena account already has a Google identity, or this Google
        # account is already linked elsewhere. Both are stated conflicts, not
        # server errors, and the message deliberately does not say which -- the
        # second case would otherwise reveal that a given Google account has an
        # Arena account.
        logger.info("Google link refused for user %s: identity already in use", usuario.id)
        await record_google_event(
            session,
            request,
            event_type="google_link_conflict",
            severity="warning",
            user_id=usuario.id,
            actor_label=usuario.email_normalizado,
            metadata={"reason": "already_linked"},
        )
        await session.commit()
        flash(
            "That Google account could not be linked. It may already be linked to an "
            "account, or your profile may already have a Google account linked.",
            FlashCategory.DANGER,
        )
        return _linked_accounts_redirect(request)

    await record_google_event(
        session,
        request,
        event_type="google_account_linked",
        user_id=usuario.id,
        actor_label=usuario.email_normalizado,
    )
    await session.commit()

    if not await user_security_notification_service.send_google_linked_email(usuario, request.app.state.email_service):
        logger.warning("Google-linked notification email failed for user %s", usuario.id)

    flash("Your Google account has been linked.", FlashCategory.SUCCESS)
    return _linked_accounts_redirect(request)


async def _complete_login(
    request: Request,
    session: AsyncSession,
    flash: FlashDep,
    *,
    claims: GoogleIdentityClaims,
    next_url: str | None,
) -> Response:
    """Log in a known Google identity, or start a Google-first signup."""
    identity = await google_identity_service.get_identity_by_sub(session, claims.sub)
    if identity is None:
        return await _start_signup(request, session, flash, claims=claims)

    usuario = await session.get(ArenaUser, identity.user_id)
    if usuario is None:
        flash(GENERIC_FAILURE_MESSAGE, FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    # An incomplete Google-first signup returning through this door resumes
    # exactly where it left off, rather than falling into the generic
    # deactivated-account message: the visitor has just re-proved control of the
    # same Google account, so this grants nothing beyond what the first visit
    # already would have. password_is_placeholder is what tells this apart from
    # an ordinary account an administrator deactivated for real, which must still
    # get that generic message.
    resume_stage = google_signup_service.describe_pending_google_signup(usuario)
    if resume_stage == "needs_completion":
        request.session[PENDING_SIGNUP_UID] = usuario.id
        return _redirect_to(request, "arena_google_complete")
    if resume_stage == "needs_guardian_consent":
        request.session[PENDING_CONSENT_UID] = usuario.id
        return _redirect_to(request, "arena_google_pending_consent")
    if resume_stage == "blocked":
        return _redirect_to(request, "arena_google_blocked")

    # The same account-state gates the password path runs, so a suspended account
    # is held here too -- this is a real deactivation, not an in-flight signup.
    gate_failure = arena_auth_service.evaluate_account_access_gates(usuario)
    if gate_failure is not None:
        logger.warning(
            "Google login blocked for user %s by account access gate: %s",
            usuario.id,
            gate_failure.status.name,
        )
        await record_google_event(
            session,
            request,
            event_type="google_login_failure",
            severity="warning",
            user_id=usuario.id,
            actor_label=usuario.email_normalizado,
            metadata={"reason": gate_failure.status.name.lower()},
        )
        await session.commit()
        gate_response = render_login_gate_failure(
            request, flash, templates=request.app.state.arena_templates, result=gate_failure
        )
        return gate_response if gate_response is not None else _redirect_to(request, "arena_login")

    await google_identity_service.update_picture_claim(session, identity, claims.picture)
    if identity.use_google_avatar and claims.picture:
        avatar_service: GoogleAvatarService = request.app.state.google_avatar_service
        try:
            await google_identity_service.refresh_google_avatar(
                session,
                identity=identity,
                user=usuario,
                avatar_service=avatar_service,
            )
        except (GoogleAvatarError, ValueError) as exc:
            logger.warning("Google avatar refresh failed for user %s: %s", usuario.id, exc)

    await google_identity_service.touch_last_login(session, identity)

    # Mirrors efetuar_login: history is recorded here for a non-2FA login, and by
    # the 2FA route once the second factor is verified.
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
            logger.error("Failed to record completed Google login for user %s", usuario.id)
            flash("We could not complete login right now. Please try again.", FlashCategory.DANGER)
            return _redirect_to(request, "arena_login")

    await record_google_event(
        session,
        request,
        event_type="google_login_success",
        user_id=usuario.id,
        actor_label=usuario.email_normalizado,
    )
    return await complete_arena_login(
        request,
        session,
        flash,
        usuario=usuario,
        jwt_service=request.app.state.jwt_service,
        remember_me=False,
        next_url=next_url or None,
        method="google",
    )


async def _start_signup(
    request: Request,
    session: AsyncSession,
    flash: FlashDep,
    *,
    claims: GoogleIdentityClaims,
) -> Response:
    """Create an inactive account for an unknown Google subject and collect the rest."""
    result = await google_signup_service.create_google_first_account(
        session,
        claims=claims,
        jwt_service=request.app.state.jwt_service,
        email_service=request.app.state.email_service,
        url_base=public_base_url(request),
    )

    if result.status == UserOperationStatus.USER_ALREADY_REGISTERED:
        # Deliberately not auto-linked. Linking is only ever done from an
        # authenticated session, so that possession of an email address alone can
        # never take over an existing Arena account.
        await record_google_event(
            session,
            request,
            event_type="google_link_conflict",
            severity="warning",
            metadata={"reason": "email_belongs_to_existing_account"},
        )
        await session.commit()
        flash(
            "An Arena account already uses that email address. Sign in with your password, "
            "then link your Google account from your profile.",
            FlashCategory.WARNING,
        )
        return _redirect_to(request, "arena_login")

    if result.status != UserOperationStatus.SUCCESS or result.user is None:
        logger.warning("Google-first signup failed with status %s", result.status.name)
        await session.rollback()
        flash(GENERIC_FAILURE_MESSAGE, FlashCategory.DANGER)
        return _redirect_to(request, "arena_login")

    await record_google_event(
        session,
        request,
        event_type="google_signup_created",
        user_id=result.user.id,
        actor_label=result.user.email_normalizado,
    )
    await session.commit()

    request.session[PENDING_SIGNUP_UID] = str(result.user.id)
    response: Response = _redirect_to(request, "arena_google_complete")
    response.background = _signup_reputation_task(request, user_id=result.user.id, email=result.user.email_normalizado)
    return response
