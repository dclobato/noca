#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Admin-facing Arena user management service.

Provides paginated user listing with filtering, role and status mutation
helpers, and other administrative operations on ArenaUser records.  All
database operations accept an ``AsyncSession`` so the caller controls the
transaction boundary.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from arena.models.arena_ai_credit_transactions import ArenaAiCreditTransaction
from arena.models.arena_users import ArenaUser
from arena.services import user_2fa_service, user_service
from arena.services.pagination_service import Pagination, PaginationParams, clamp_page
from shared.enumerations import ArenaRole

logger = logging.getLogger(__name__)

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
        search: Optional search string matched against name and email fields.
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
        conditions.append(
            or_(
                ArenaUser.nome.ilike(term),
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


async def toggle_ranking_visible(usuario: ArenaUser, session: AsyncSession) -> None:
    """Toggle the public-ranking visibility flag for an Arena user.

    When ``ranking_visible`` is False the user's name and rating are hidden from
    all public ranking lists and excluded from affiliation rating computation.
    The user's own rating is still computed and visible on their profile.

    Args:
        usuario: Arena user to toggle.
        session: Active async database session.
    """
    usuario.ranking_visible = not usuario.ranking_visible
    await session.flush()
    logger.info(
        "Set ranking_visible=%s for %s",
        usuario.ranking_visible,
        usuario.email,
    )


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


async def admin_toggle_parental_consent(usuario: ArenaUser, session: AsyncSession) -> None:
    """Toggle the parental-consent flag for an Arena user.

    Granting sets the flag and records the current timestamp.  Revoking clears
    both the flag and its timestamp.

    Args:
        usuario: Arena user whose parental consent state will be toggled.
        session: Active async database session.
    """
    if not usuario.consentimento_responsavel:
        usuario.consentimento_responsavel = True
        usuario.dta_consentimento_responsavel = datetime.now(UTC)
        await session.flush()
        logger.warning("Admin granted parental consent for %s", usuario.email)
    else:
        usuario.consentimento_responsavel = False
        usuario.dta_consentimento_responsavel = None
        await session.flush()
        logger.warning("Admin revoked parental consent for %s", usuario.email)


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
