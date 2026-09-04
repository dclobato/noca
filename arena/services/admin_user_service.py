#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Admin-facing Arena user management service.

Provides paginated user listing with filtering, role and status mutation
helpers, and other administrative operations on ArenaUser records.  All
database operations accept an ``AsyncSession`` so the caller controls the
transaction boundary.

One boundary is worth stating up front: **an admin may not override the age
shield.** Publishing a 13-17 year-old's legal name or profile page is refused
here exactly as it is on the user's own path, because that gate is a legal
control rather than a moderation control. For the same reason there is no admin
toggle for ``full_name_public`` at all -- it is an adult's own opt-in to publish
their own name, and an administrator making that choice on someone's behalf is
not an administrative act. What an admin *can* do is rename an account, which
bypasses the change cooldown and is audited.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from arena.models.arena_ai_credit_transactions import ArenaAiCreditTransaction
from arena.models.arena_user_google_identity import ArenaUserGoogleIdentity
from arena.models.arena_users import ArenaUser
from arena.services import google_identity_service, user_2fa_service, user_service, username_service
from arena.services.pagination_service import Pagination, PaginationParams, clamp_page
from arena.services.user_visibility_service import may_have_public_profile
from shared.enumerations import ArenaRole

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConsentToggleOutcome:
    """Result of an admin parental-consent toggle.

    Attributes:
        granted: ``True`` when the toggle granted consent, ``False`` when it withdrew it.
        activated: ``True`` only when the grant moved the account from inactive to
            active, so the caller does not record an activation that did not happen.
    """

    granted: bool
    activated: bool


ARENA_ROLE_DISPLAY: dict[ArenaRole, str] = {
    ArenaRole.ARENA_ADMIN: "Arena Admin",
    ArenaRole.ARENA_JUDGE: "Judge",
    ArenaRole.ARENA_USER: "Regular User",
}


async def list_users_paginated(
    session: AsyncSession,
    *,
    page: int,
    per_page: int,
    search: str = "",
    role_filter: ArenaRole | None = None,
    can_edit_only: bool = False,
) -> Pagination[ArenaUser]:
    """Return a paginated list of Arena users with optional filtering.

    Args:
        session: Active async database session.
        page: Requested page number (one-based).
        per_page: Number of items per page.
        search: Optional search string matched against the legal name, the public
            handle, the account email, and the guardian email.
        role_filter: When set, restricts results to users with this role.
        can_edit_only: When ``True``, restricts results to users with problem-edit
            permission — ``ARENA_ADMIN`` (always allowed) or ``can_edit=True``.

    Returns:
        Pagination[ArenaUser]: Paginated result of matching Arena users.
    """
    params = PaginationParams(page=page, per_page=per_page)

    conditions: list[ColumnElement[bool]] = []
    if search.strip():
        term = f"%{search.strip()}%"
        # The handle is searchable here on purpose. The age shield is a *public*
        # read-path rule -- it governs what anonymous and peer surfaces publish --
        # and this is the admin console, where an operator acting on a report
        # only ever has the handle to go on, since the handle is the only name
        # the reporter could have seen.
        conditions.append(
            or_(
                ArenaUser.nome.ilike(term),
                ArenaUser.username.ilike(term),
                ArenaUser.email_normalizado.ilike(term),
                ArenaUser.email_responsavel_legal.ilike(term),
            )
        )
    if role_filter is not None:
        conditions.append(ArenaUser.role == role_filter)
    if can_edit_only:
        conditions.append(or_(ArenaUser.role == ArenaRole.ARENA_ADMIN, ArenaUser.can_edit.is_(True)))

    count_query = select(func.count()).select_from(ArenaUser).where(*conditions)
    total: int = (await session.execute(count_query)).scalar() or 0

    data_query = (
        select(ArenaUser).where(*conditions).order_by(ArenaUser.nome.asc()).offset(params.offset).limit(params.per_page)
    )
    users = list((await session.execute(data_query)).scalars().all())

    return Pagination(items=users, page=params.page, per_page=params.per_page, total=total)


async def count_admins(session: AsyncSession) -> int:
    """Return the number of Arena users with the ARENA_ADMIN role.

    Args:
        session: Active async database session.

    Returns:
        int: Count of admin users.
    """
    result = await session.execute(
        select(func.count()).select_from(ArenaUser).where(ArenaUser.role == ArenaRole.ARENA_ADMIN)
    )
    return result.scalar() or 0


async def change_role(usuario: ArenaUser, new_role: ArenaRole, session: AsyncSession) -> None:
    """Assign a new role to an Arena user.

    Args:
        usuario: Arena user whose role will be changed.
        new_role: The new ArenaRole to assign.
        session: Active async database session.
    """
    usuario.role = new_role
    await session.flush()
    logger.info("Changed role of %s to %s", usuario.email, new_role)


async def toggle_active(usuario: ArenaUser, session: AsyncSession) -> None:
    """Toggle the active status of an Arena user.

    Deactivating an account also invalidates all existing sessions.
    Reactivating an account calls ativar_conta without invalidating sessions.

    Args:
        usuario: Arena user to toggle.
        session: Active async database session.
    """
    if usuario.ativo:
        await user_service.desativar_conta(usuario, session)
        await user_service.invalidate_sessions(usuario, session)
        logger.info("Deactivated and invalidated sessions for %s", usuario.email)
    else:
        await user_service.ativar_conta(usuario, session)
        logger.info("Activated account for %s", usuario.email)


async def toggle_force_password_change(usuario: ArenaUser, session: AsyncSession) -> None:
    """Toggle the forced password-change flag for an Arena user.

    If the flag is not set, marks the user for a mandatory password change.
    If it is already set, clears the flag and its associated timestamp.

    Args:
        usuario: Arena user to toggle.
        session: Active async database session.
    """
    if not usuario.precisa_trocar_senha:
        await user_service.marcar_para_trocar_senha(usuario, session)
    else:
        usuario.precisa_trocar_senha = False
        usuario.dta_marcacao_troca_senha = None
        await session.flush()
        logger.info("Cleared forced password-change flag for %s", usuario.email)


async def toggle_can_edit(usuario: ArenaUser, session: AsyncSession) -> None:
    """Toggle the problem-base edit privilege for an Arena user.

    Flips the ``can_edit`` flag that grants permission to add/edit problems on
    the Arena problem base. Administrators may always edit problems regardless of
    this flag, so this is only meaningful for non-admin users.

    Args:
        usuario: Arena user to toggle.
        session: Active async database session.
    """
    usuario.can_edit = not usuario.can_edit
    await session.flush()
    logger.info(
        "Set can_edit=%s for %s",
        usuario.can_edit,
        usuario.email,
    )


async def toggle_ranking_visible(usuario: ArenaUser, session: AsyncSession) -> bool:
    """Toggle the public-ranking visibility flag for an Arena user.

    When ``ranking_visible`` is False the user's name and rating are hidden from
    all public ranking lists and excluded from affiliation rating computation.
    The user's own rating is still computed and visible on their profile.

    If the toggle hides the user from the ranking, any existing
    ``public_profile`` opt-in is cleared automatically because a public
    profile page is meaningless without ranking visibility.

    Args:
        usuario: Arena user to toggle.
        session: Active async database session.

    Returns:
        bool: True when ``public_profile`` was also cleared as a side-effect.
    """
    usuario.ranking_visible = not usuario.ranking_visible
    public_profile_cleared = False
    if not usuario.ranking_visible and usuario.public_profile:
        usuario.public_profile = False
        public_profile_cleared = True
        logger.info("Auto-cleared public_profile for %s (ranking_visible set to False)", usuario.email)
    await session.flush()
    logger.info(
        "Set ranking_visible=%s for %s",
        usuario.ranking_visible,
        usuario.email,
    )
    return public_profile_cleared


async def toggle_public_profile(usuario: ArenaUser, session: AsyncSession) -> str | None:
    """Toggle the public-profile opt-in flag for an Arena user.

    Enabling ``public_profile`` is refused on two independent grounds, each
    returning a human-readable reason for the caller to flash:

    - ``ranking_visible`` is False -- a public profile page is meaningless when
      the user does not appear in the ranking at all.
    - the account is **age-shielded** (13-17, or an unrecorded date of birth,
      which fails closed). An administrator may not override this. It is not a
      moderation decision they are entitled to make differently from the user:
      it is the same legal control the user's own path enforces, and an admin
      route weaker than the user route it mirrors is simply a way around the
      shield.

    Disabling is never refused on either ground, so an account that became
    shielded while its flag was set can always be brought back into line.

    Args:
        usuario: Arena user to toggle.
        session: Active async database session.

    Returns:
        str | None: Error message when the toggle is blocked, otherwise ``None``.
    """
    if not usuario.public_profile and not usuario.ranking_visible:
        return "Public profile requires ranking visibility to be enabled first."
    if not usuario.public_profile and not may_have_public_profile(usuario):
        return (
            "This account is age-shielded (under 18, or no date of birth on record), "
            "so its profile cannot be made public. This is a legal control and cannot "
            "be overridden by an administrator."
        )
    usuario.public_profile = not usuario.public_profile
    await session.flush()
    logger.info("Set public_profile=%s for %s", usuario.public_profile, usuario.email)
    return None


async def admin_remove_photo(usuario: ArenaUser, session: AsyncSession) -> None:
    """Remove the profile photo for an Arena user.

    Args:
        usuario: Arena user whose photo should be removed.
        session: Active async database session.
    """
    usuario.clear_foto_fields()
    await session.flush()
    logger.info("Removed photo for %s", usuario.email)


async def admin_disable_2fa(usuario: ArenaUser, session: AsyncSession) -> None:
    """Disable two-factor authentication for an Arena user.

    Also invalidates all existing sessions so the user must log in again.

    Args:
        usuario: Arena user whose 2FA should be disabled.
        session: Active async database session.
    """
    await user_2fa_service.desativar_2fa(usuario, session)
    await user_service.invalidate_sessions(usuario, session)
    logger.warning("Admin disabled 2FA and invalidated sessions for %s", usuario.email)


async def admin_unlink_google(usuario: ArenaUser, identity: ArenaUserGoogleIdentity, session: AsyncSession) -> None:
    """Detach a Google identity from an Arena user on an administrator's behalf.

    Deliberately **not** subject to the last-method guard the self-service
    unlink applies. That guard protects a user from locking *themselves* out
    by accident; an administrator detaching a credential -- because the Google
    account was lost, compromised, or attached to the wrong person -- is doing
    it on purpose, and the account keeps its ordinary recovery path: the
    password reset flow, which sets a real password over the placeholder. The
    caller is responsible for telling the user so.

    Existing sessions are invalidated, as ``admin_disable_2fa`` does: a
    credential an administrator strips may be one an attacker is sitting on.

    Args:
        usuario: Arena user whose Google identity should be removed.
        identity: That user's linked Google identity row.
        session: Active async database session.
    """
    if identity.use_google_avatar:
        usuario.bump_avatar_revision()
    await google_identity_service.unlink_identity(session, identity)
    await user_service.invalidate_sessions(usuario, session)
    logger.warning("Admin unlinked the Google account and invalidated sessions for %s", usuario.email)


async def admin_change_name(usuario: ArenaUser, new_name: str, session: AsyncSession) -> None:
    """Update the display name of an Arena user.

    Args:
        usuario: Arena user whose name should be changed.
        new_name: New display name (will be stripped of leading/trailing whitespace).
        session: Active async database session.

    Raises:
        ValueError: If the stripped name is empty.
    """
    stripped = new_name.strip()
    if not stripped:
        raise ValueError("Name cannot be empty")
    usuario.nome = stripped
    await session.flush()
    logger.info("Changed name of %s to %r", usuario.email, stripped)


async def admin_change_username(
    usuario: ArenaUser,
    new_username: str,
    session: AsyncSession,
    *,
    allow_immediate_change: bool = False,
) -> str:
    """Rename an Arena user's public handle, bypassing the change cooldown.

    The cooldown protects a shielded user's pseudonymity from *their own* churn;
    an administrator acting on a report (an offensive or impersonating handle)
    is the case it was never meant to block, so it is bypassed here. The route
    that calls this re-confirms the admin's password and writes both an
    ``admin_action`` audit row and a ``username_changed`` security event.

    **What happens to the user's own cooldown afterwards is the admin's call.**
    By default the rename starts a fresh window, which is right for a moderation
    rename: a handle taken down after a report must not be restored a moment
    later. ``allow_immediate_change`` inverts that for the benign cases -- a
    typo, or a rename the user asked for -- where the default would instead lock
    someone out of choosing their own name for a full window over something they
    did not do. Only the administrator knows which situation this is, so only the
    administrator can say.

    The previous handle is returned rather than discarded so the caller can put
    both names in that audit metadata. Linking the old handle to the new one is
    exactly what the cooldown denies an outside observer, which is why it belongs
    in the admin-only audit log and nowhere else -- a trail that cannot connect
    the two names records nothing useful.

    Args:
        usuario: Arena user being renamed.
        new_username: The handle as submitted by the administrator.
        session: Active async database session.
        allow_immediate_change: True to leave the user free to rename again at
            once, instead of starting a fresh cooldown window.

    Returns:
        str: The handle the user held **before** this change.

    Raises:
        UsernameError: If the handle is malformed, reserved, or already taken.
    """
    previous = usuario.username
    await username_service.change_username(
        session,
        usuario,
        new_username,
        bypass_cooldown=True,
        clear_cooldown=allow_immediate_change,
    )
    logger.warning(
        "Admin changed username of %s from %r to %r (allow_immediate_change=%s)",
        usuario.email,
        previous,
        usuario.username,
        allow_immediate_change,
    )
    return previous


async def admin_remove_location(usuario: ArenaUser, session: AsyncSession) -> None:
    """Clear the country and subdivision location data for an Arena user.

    Args:
        usuario: Arena user whose location should be removed.
        session: Active async database session.
    """
    usuario.country_code = None
    usuario.subdivision_code = None
    await session.flush()
    logger.info("Removed location for %s", usuario.email)


async def admin_remove_affiliation(usuario: ArenaUser, session: AsyncSession) -> None:
    """Detach an Arena user from their current affiliation.

    Args:
        usuario: Arena user whose affiliation should be removed.
        session: Active async database session.
    """
    usuario.affiliation_id = None
    await session.flush()
    logger.info("Removed affiliation for %s", usuario.email)


async def admin_reset_api_key(usuario: ArenaUser, session: AsyncSession) -> None:
    """Clear the personal AI API key for an Arena user.

    Args:
        usuario: Arena user whose API key should be cleared.
        session: Active async database session.
    """
    usuario.ai_api_key = None
    await session.flush()
    logger.warning("Admin cleared personal AI API key for %s", usuario.email)


async def admin_toggle_email_confirmed(usuario: ArenaUser, session: AsyncSession) -> None:
    """Toggle the email-confirmed flag for an Arena user.

    Confirming delegates to ``user_service.confirmar_email`` which sets the
    timestamp.  Unconfirming clears both the flag and its timestamp.

    Args:
        usuario: Arena user whose email confirmation state will be toggled.
        session: Active async database session.
    """
    if not usuario.email_confirmado:
        await user_service.confirmar_email(usuario, session)
        logger.warning("Admin confirmed email for %s", usuario.email)
    else:
        usuario.email_confirmado = False
        usuario.dta_validacao_email = None
        await session.flush()
        logger.warning("Admin cleared email confirmation for %s", usuario.email)


async def admin_toggle_parental_consent(usuario: ArenaUser, session: AsyncSession) -> ConsentToggleOutcome:
    """Toggle the parental-consent flag for an Arena user.

    Both branches route through the same ``user_service`` write paths the guardian's own
    link uses, so the two can never drift: an admin revocation deactivates the account and
    kills live sessions exactly as a guardian revocation does, rather than flipping a flag
    and leaving a suspended minor with a working JWT. Granting likewise re-activates the
    account when the remaining gates are clear, which is what makes an admin toggle a real
    recovery from a revocation rather than half of one.

    Both branches bump ``consent_generation``, so any revocation link outstanding in a
    guardian's mailbox becomes inert the moment an admin touches the consent state.

    Args:
        usuario: Arena user whose parental consent state will be toggled.
        session: Active async database session.

    Returns:
        ConsentToggleOutcome: What the toggle did, for the route to audit and report.
    """
    if not usuario.consentimento_responsavel:
        was_active = usuario.ativo
        await user_service.grant_parental_consent(usuario, session)
        activated = await user_service.ativar_conta_se_pronta(usuario, session) and not was_active
        logger.warning("Admin granted parental consent for %s", usuario.email)
        return ConsentToggleOutcome(granted=True, activated=activated)
    await user_service.revoke_parental_consent(usuario, session)
    logger.warning("Admin revoked parental consent for %s", usuario.email)
    return ConsentToggleOutcome(granted=False, activated=False)


async def get_credit_transactions_paginated(
    session: AsyncSession,
    user_id: str,
    *,
    params: PaginationParams,
) -> Pagination[ArenaAiCreditTransaction]:
    """Return a paginated, reverse-chronological list of AI credit transactions for a user.

    Args:
        session: Active async database session.
        user_id: Id of the Arena user whose transactions to list.
        params: Pagination parameters (page, per_page).

    Returns:
        Pagination[ArenaAiCreditTransaction]: Page of transactions with submission and admin
            relationships eager-loaded.
    """
    count_query = (
        select(func.count()).select_from(ArenaAiCreditTransaction).where(ArenaAiCreditTransaction.user_id == user_id)
    )
    total: int = (await session.execute(count_query)).scalar() or 0

    effective_page = clamp_page(params.page, total=total, per_page=params.per_page)
    effective_params = PaginationParams(page=effective_page, per_page=params.per_page)

    data_query = (
        select(ArenaAiCreditTransaction)
        .options(
            selectinload(ArenaAiCreditTransaction.submission),
            selectinload(ArenaAiCreditTransaction.admin),
        )
        .where(ArenaAiCreditTransaction.user_id == user_id)
        .order_by(ArenaAiCreditTransaction.created_at.desc())
        .offset(effective_params.offset)
        .limit(effective_params.per_page)
    )
    items = list((await session.execute(data_query)).scalars().all())

    return Pagination(items=items, page=effective_page, per_page=effective_params.per_page, total=total)
