#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Account creation for a Google-first Arena signup.

Google can prove who someone is, but not the two things Arena is legally obliged
to know before an account may be used: the date of birth the LGPD age gate turns
on, and acceptance of the Terms of Service. So an unknown Google subject creates
an account that is deliberately *unusable* -- ``ativo=False``, no date of birth,
terms not accepted -- and the completion step collects the rest.

That ordering matters. The account and its identity row are written in one
transaction, so a crash between them cannot leave a Google account that maps to
nothing, or an Arena account nobody can reach. An abandoned completion leaves an
inactive account that fails ``evaluate_account_access_gates`` on every path, which
is the safe direction.
"""

from __future__ import annotations

import logging
import secrets

from jwtservice import JWTService
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from arena.models.arena_user_google_identity import ArenaUserGoogleIdentity
from arena.models.arena_users import ArenaUser
from arena.services import google_identity_service, user_registration_service
from arena.services.google_oauth_service import GoogleIdentityClaims
from arena.services.user_service import UserOperationStatus, UserServiceResult
from shared.age_check import AgeStatus, check_age
from shared.db_schema.arena import arena_users as arena_users_table
from shared.services.email_service import EmailService

logger = logging.getLogger(__name__)

# Long enough that the resulting Werkzeug hash can never be matched by a guess.
# The hash is real, so get_token_id() and session_version behave exactly as for
# any other account; password_is_placeholder is what records that it is unusable.
_PLACEHOLDER_PASSWORD_BYTES = 64


def _display_name(claims: GoogleIdentityClaims) -> str:
    """Pick the name to seed the new account with.

    Google's ``name`` claim is optional. Falling back to the local part of the
    address keeps signup working rather than failing on a missing optional claim;
    the user can correct it from their profile.

    Args:
        claims: Verified Google claims.

    Returns:
        str: A non-empty display name.
    """
    if claims.name:
        return claims.name.strip()[:180]
    if claims.email:
        return claims.email.split("@", 1)[0][:180]
    return "Arena user"


async def create_google_first_account(
    session: AsyncSession,
    *,
    claims: GoogleIdentityClaims,
    jwt_service: JWTService,
    email_service: EmailService,
    url_base: str,
) -> UserServiceResult:
    """Create an inactive Arena account and link it to a Google identity.

    The account is created with a random unusable password, an already-confirmed
    email (Google asserted ``email_verified``, which the caller checked), and no
    date of birth or terms acceptance -- the completion step supplies those.

    ``password_is_placeholder`` is set *after* ``registrar_usuario`` returns,
    because that function assigns through the ``password`` setter, which clears
    the flag.

    Args:
        session: Active async database session; the caller commits.
        claims: Verified Google claims.
        jwt_service: Arena JWT service, required by the registration service.
        email_service: Arena email service, required by the registration service.
        url_base: Public base URL, required by the registration service.

    Returns:
        UserServiceResult: ``SUCCESS`` with the new user, or the registration
            service's own failure status -- notably ``USER_ALREADY_REGISTERED``
            when the Google address belongs to an existing Arena account, which
            the caller must not silently auto-link.
    """
    if not claims.email:
        logger.warning("Google-first signup refused: the token carried no email claim")
        return UserServiceResult(
            status=UserOperationStatus.INVALID_EMAIL,
            error_message="Google did not provide an email address.",
        )

    result = await user_registration_service.registrar_usuario(
        nome=_display_name(claims),
        email=claims.email,
        password=secrets.token_urlsafe(_PLACEHOLDER_PASSWORD_BYTES),
        session=session,
        jwt_service=jwt_service,
        email_service=email_service,
        url_base=url_base,
        ativo=False,
        email_confirmado=True,
        enviar_email=False,
        dta_nascimento=None,
        consentimento_responsavel=False,
        aceitou_termos_privacidade=False,
    )
    if result.status != UserOperationStatus.SUCCESS or result.user is None:
        return result

    result.user.password_is_placeholder = True
    await session.flush()

    # Same transaction as the user row on purpose: a Google identity that pointed
    # at a user that was never committed would be unreachable and unrecoverable.
    await google_identity_service.link_identity(
        session,
        user_id=result.user.id,
        claims=claims,
    )
    logger.info("Created Arena account %s from a Google-first signup", result.user.id)
    return result


def describe_pending_google_signup(usuario: ArenaUser) -> str | None:
    """Classify an inactive account as an in-flight Google-first signup, or not.

    ``password_is_placeholder`` is the discriminator, not ``ativo`` alone: an
    administrator can deactivate an ordinary password account too, and that
    account must still get the generic "deactivated, contact support" message
    rather than being swept into a flow it never went through. Only an account
    whose password is the random one this module minted is eligible to resume
    here.

    Args:
        usuario: The account a Google login just resolved to.

    Returns:
        str | None: ``"needs_completion"`` when the date of birth was never
            collected, ``"blocked"`` when it was and the account is under 13,
            ``"needs_guardian_consent"`` when it was and a guardian has not yet
            consented, or ``None`` when this account is not an incomplete
            Google-first signup at all.
    """
    if usuario.ativo or not usuario.password_is_placeholder:
        return None
    if usuario.dta_nascimento is None:
        return "needs_completion"
    age_status = check_age(usuario.dta_nascimento)
    if age_status == AgeStatus.BLOCKED:
        return "blocked"
    if age_status == AgeStatus.NEEDS_PARENTAL_CONSENT and not usuario.consentimento_responsavel:
        return "needs_guardian_consent"
    return None


async def transfer_pending_signup_identity(
    session: AsyncSession,
    *,
    orphan: ArenaUser,
    user_id: str,
) -> ArenaUserGoogleIdentity:
    """Move a never-completed Google-first signup's identity to an existing account.

    The "I already have an account" outcome. A Google-first signup whose address
    differs from the user's real Arena address creates an account that
    permanently holds the Google subject, so the real account can never link it.
    This folds that orphan into the account the user has just authenticated to:
    the identity row is re-created under ``user_id`` and the orphan row is
    deleted, in the caller's transaction, so a failure leaves both exactly as
    they were.

    Deleting the orphan is safe because a ``needs_completion`` account has nothing
    in it -- created inactive with no date of birth, no terms acceptance, and no
    way to have submitted anything -- and it is guarded anyway: any other state is
    refused, so a completed account can never be removed by this path. The row is
    deleted with a Core statement rather than ``session.delete``, because the ORM
    mapping cascades through relationships the async session cannot lazily load;
    the database cascades take the orphan's satellites with it.

    Args:
        session: Active async database session; the caller commits.
        orphan: The never-completed Google-first account.
        user_id: The authenticated account that should hold the identity.

    Returns:
        ArenaUserGoogleIdentity: The identity now linked to ``user_id``.

    Raises:
        ValueError: If ``orphan`` is not a never-completed Google-first signup or
            holds no Google identity.
        sqlalchemy.exc.IntegrityError: If ``user_id`` already has a Google
            identity; the caller presents it as a stated conflict.
    """
    if describe_pending_google_signup(orphan) != "needs_completion":
        raise ValueError("Only a never-completed Google-first signup can be folded into another account.")
    identity = await google_identity_service.get_identity_for_user(session, orphan.id)
    if identity is None:
        raise ValueError("The pending Google-first signup holds no Google identity.")

    claims = GoogleIdentityClaims(
        sub=identity.google_sub,
        email=identity.google_email,
        email_verified=identity.google_email_verified,
        name=None,
        picture=identity.google_picture_url,
    )
    orphan_id = orphan.id
    # The subject's UNIQUE constraint means the orphan's row must be gone before
    # the same subject can be inserted under the real account.
    await google_identity_service.unlink_identity(session, identity)
    session.expunge(orphan)
    await session.execute(delete(arena_users_table).where(arena_users_table.c.id == orphan_id))
    await session.flush()
    logger.info("Discarded never-completed Google-first account %s in favour of user %s", orphan_id, user_id)
    return await google_identity_service.link_identity(session, user_id=user_id, claims=claims)


_ADMIN_UNLINK_REFUSALS: dict[str, str] = {
    "needs_completion": (
        "This Google-first signup was never finished. The Google account is the only "
        "identity the row has: unlinking it would leave an account that neither door can "
        "complete, while still blocking a new signup with the same address."
    ),
    "blocked": (
        "This account was refused for being under 13 and stays inactive. Signing in with "
        "Google is what brings the user to the page explaining that refusal; without the "
        'identity, the next Google sign-in would end at a dead-end "email already '
        'registered" message, since the address stays taken and the account has no password.'
    ),
    "needs_guardian_consent": (
        "This account is waiting for a guardian's consent, and signing in with Google is "
        "how the user reaches the consent flow. It can be unlinked once consent is granted "
        "and the account is active again."
    ),
}


def admin_unlink_refusal(usuario: ArenaUser) -> str | None:
    """Return why an administrator may not unlink this account's Google identity, or None.

    The admin unlink skips the last-method guard on the strength of one claim:
    the account keeps its recovery path, the ordinary password reset. That claim
    holds for a *completed* Google-only account and fails for every in-flight
    Google-first signup :func:`describe_pending_google_signup` names. A never-
    completed row is inactive with no date of birth, so a freshly set password
    logs in to the generic "deactivated" refusal -- the age and terms step is
    behind the inactive check -- while the Google door can no longer resume it
    and a fresh signup with the same address is refused as already registered.
    A 13-17 account held for consent -- an unfinished signup, or a completed one
    whose consent was withdrawn -- reaches the consent flow only through the
    Google callback. An under-13 refusal is enforced by the date of birth, not by
    the identity row; but the identity row is what routes the user to the page
    explaining it, and without it the next Google sign-in ends at the same
    dead-end "already registered" message as the first case.

    Args:
        usuario: The account whose Google identity an administrator wants to detach.

    Returns:
        str | None: A human-readable refusal for an unfinished Google-first
            signup, or ``None`` when the unlink may proceed.
    """
    pending = describe_pending_google_signup(usuario)
    return None if pending is None else _ADMIN_UNLINK_REFUSALS[pending]
