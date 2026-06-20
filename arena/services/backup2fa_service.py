#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Backup 2FA code management for Arena users.

Generates, validates, and invalidates single-use backup codes stored in
``arena_backup_2fa``.  All methods accept an ``AsyncSession`` so the caller
controls the transaction boundary — no implicit commits are performed here.
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from werkzeug.security import check_password_hash, generate_password_hash

from arena.models.arena_auth_records import ArenaBackup2FA
from arena.models.arena_users import ArenaUser

_CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
_CODE_LENGTH = 6


class Backup2FAServiceError(Exception):
    """Raised when a backup-code operation fails."""


def _generate_code() -> str:
    return "".join(secrets.choice(_CHARSET) for _ in range(_CODE_LENGTH))


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def consumir_token(
    usuario: ArenaUser,
    token: str,
    session: AsyncSession,
    keep_for_days: int = 30,
) -> bool:
    """Verify and consume a backup code, marking it as used.

    Args:
        usuario: Arena user who is claiming the backup code.
        token: Plaintext backup code supplied by the user.
        session: Active async database session.
        keep_for_days: Days to retain the record after use before hard deletion.

    Returns:
        bool: ``True`` if a matching unused code was found and consumed.

    Raises:
        Backup2FAServiceError: On unexpected database errors.
    """
    try:
        result = await session.execute(
            select(ArenaBackup2FA).where(
                ArenaBackup2FA.arena_user_id == usuario.id,
                ArenaBackup2FA.utilizado.is_(False),
            )
        )
        codigos = result.scalars().all()
        for codigo in codigos:
            if check_password_hash(codigo.hash_codigo, token):
                codigo.utilizado = True
                codigo.dta_uso = _utcnow()
                codigo.dta_para_remocao = _utcnow() + timedelta(days=keep_for_days)
                return True
        return False
    except SQLAlchemyError as exc:
        raise Backup2FAServiceError(f"Error consuming backup token: {exc}") from exc


async def contar_tokens_disponiveis(usuario: ArenaUser, session: AsyncSession) -> int:
    """Count unused backup codes for the given user.

    Args:
        usuario: Arena user to check.
        session: Active async database session.

    Returns:
        int: Number of unused backup codes.
    """
    result = await session.execute(
        select(func.count())
        .select_from(ArenaBackup2FA)
        .where(
            ArenaBackup2FA.arena_user_id == usuario.id,
            ArenaBackup2FA.utilizado.is_(False),
        )
    )
    return result.scalar() or 0


async def invalidar_codigos(
    usuario: ArenaUser,
    session: AsyncSession,
    keep_for_days: int = 30,
) -> int:
    """Soft-delete all unused backup codes by marking them as used.

    Sets ``utilizado=True`` and schedules deletion after ``keep_for_days``.
    Call this when 2FA is disabled or before generating fresh codes.

    Args:
        usuario: Arena user whose codes should be invalidated.
        session: Active async database session.
        keep_for_days: Days to retain invalidated records before hard deletion.

    Returns:
        int: Number of codes that were invalidated.

    Raises:
        Backup2FAServiceError: On unexpected database errors.
    """
    try:
        result = await session.execute(
            select(ArenaBackup2FA).where(
                ArenaBackup2FA.arena_user_id == usuario.id,
                ArenaBackup2FA.utilizado.is_(False),
            )
        )
        codigos = result.scalars().all()
        now = _utcnow()
        removal = now + timedelta(days=keep_for_days)
        for codigo in codigos:
            codigo.utilizado = True
            codigo.dta_uso = now
            codigo.dta_para_remocao = removal
        return len(codigos)
    except SQLAlchemyError as exc:
        raise Backup2FAServiceError(f"Error invalidating backup codes: {exc}") from exc


async def gerar_novos_codigos(
    usuario: ArenaUser,
    session: AsyncSession,
    quantidade: int = 10,
) -> list[str]:
    """Invalidate existing codes and generate a fresh set.

    Args:
        usuario: Arena user for whom new codes are generated.
        session: Active async database session.
        quantidade: Number of new codes to generate (clamped to 1–20).

    Returns:
        list[str]: Plaintext codes to display once to the user.

    Raises:
        Backup2FAServiceError: On unexpected database errors.
    """
    quantidade = max(1, min(quantidade, 20))
    try:
        await invalidar_codigos(usuario, session)
        codigos_texto_plano: list[str] = []
        now = _utcnow()
        for _ in range(quantidade):
            codigo_plano = _generate_code()
            codigos_texto_plano.append(codigo_plano)
            session.add(
                ArenaBackup2FA(
                    id=str(uuid.uuid4()),
                    arena_user_id=usuario.id,
                    hash_codigo=generate_password_hash(codigo_plano),
                    utilizado=False,
                    created_at=now,
                )
            )
        return codigos_texto_plano
    except Backup2FAServiceError:
        raise
    except SQLAlchemyError as exc:
        raise Backup2FAServiceError(f"Error generating backup codes: {exc}") from exc


async def remover_codigos_expirados(session: AsyncSession) -> int:
    """Hard-delete backup code records whose scheduled removal date has passed.

    Intended to be called by a periodic background task (e.g. once per day).

    Args:
        session: Active async database session.

    Returns:
        int: Number of records deleted.

    Raises:
        Backup2FAServiceError: On unexpected database errors.
    """
    try:
        result = await session.execute(
            delete(ArenaBackup2FA).where(
                ArenaBackup2FA.dta_para_remocao.isnot(None),
                ArenaBackup2FA.dta_para_remocao <= _utcnow(),
            )
        )
        return result.rowcount  # type: ignore[attr-defined, no-any-return]
    except SQLAlchemyError as exc:
        raise Backup2FAServiceError(f"Error removing expired backup codes: {exc}") from exc
