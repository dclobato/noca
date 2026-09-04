#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Arena-side vocabulary for the administrative lockout reset.

The shared primitive (:mod:`shared.services.auth_lockout_admin`) works on
account *hashes*; this module knows which raw identifiers Arena's own
throttle buckets hash for one account -- the login form's email, the
normalised and canonical addresses, the user id the password and TOTP
re-verification routes key on, and the ``sub:`` subjects the activation and
parental-consent links carry -- so "unlock this user" reaches every bucket
that person can be locked in. Arena admins clear Arena buckets only.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from arena.models.arena_users import ArenaUser
from shared.services.auth_lockout_admin import (
    ActiveLockout,
    LockoutStoreClient,
    LockoutStoreUnavailableError,
    LockoutSubject,
    account_identifier_hashes,
    describe_lockouts,
)
from shared.services.email_validation import EmailValidationService

__all__ = [
    "ARENA_LOCKOUT_MODULES",
    "ResolvedIdentifier",
    "describe_user_lockouts",
    "hashes_for_user",
    "identifiers_for_user",
    "resolve_identifier",
    "subject_for_hashes",
    "subject_for_ip",
]

ARENA_LOCKOUT_MODULES: tuple[str, ...] = ("arena",)
"""The only key module an Arena administrator may clear."""


@dataclass(frozen=True, slots=True)
class ResolvedIdentifier:
    """What a typed identifier turned out to name."""

    user: ArenaUser | None
    hashes: frozenset[str]


def identifiers_for_user(user: ArenaUser) -> list[str | None]:
    """Every raw identifier Arena's throttle buckets may have hashed for ``user``."""
    return [
        user.email_normalizado,
        user.email_canonical,
        user.id,
        f"sub:{user.id}",
        f"sub:{user.email_normalizado}",
    ]


def hashes_for_user(user: ArenaUser) -> frozenset[str]:
    """Throttle hashes of every identifier in :func:`identifiers_for_user`."""
    return account_identifier_hashes(identifiers_for_user(user), secret=settings.JWT_SECRET_KEY)


async def resolve_identifier(session: AsyncSession, raw: str) -> ResolvedIdentifier:
    """Turn a typed identifier into the hashes to clear.

    The raw text is always hashed, because that is what the login and signup
    forms hash. When it is an address of a known account, that account's full
    recipe is added, so one entry lifts every bucket of that user.
    """
    text = raw.strip()
    identifiers: list[str | None] = [text]
    user: ArenaUser | None = None
    try:
        normalized = EmailValidationService.normalize(text)
    except ValueError:
        normalized = None
    if normalized is not None:
        user = await session.scalar(select(ArenaUser).where(ArenaUser.email_normalizado == normalized))
    if user is not None:
        identifiers.extend(identifiers_for_user(user))
    return ResolvedIdentifier(user=user, hashes=account_identifier_hashes(identifiers, secret=settings.JWT_SECRET_KEY))


def subject_for_ip(ip: str) -> LockoutSubject:
    """The Arena-scoped subject for one validated address."""
    return LockoutSubject(modules=ARENA_LOCKOUT_MODULES, ip=ip)


def subject_for_hashes(hashes: frozenset[str]) -> LockoutSubject:
    """The Arena-scoped subject for a set of account hashes."""
    return LockoutSubject(modules=ARENA_LOCKOUT_MODULES, identifier_hashes=hashes)


async def describe_user_lockouts(store: LockoutStoreClient | None, user: ArenaUser) -> list[ActiveLockout] | None:
    """Live locks of ``user``, or ``None`` when the store cannot answer."""
    try:
        return await describe_lockouts(store, subject_for_hashes(hashes_for_user(user)))
    except LockoutStoreUnavailableError:
        return None
