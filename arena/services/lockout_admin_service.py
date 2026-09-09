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
from arena.services.user_throttle_hash_service import (
    current_secret_version_id,
    hashes_for_identity,
    identifiers_for_identity,
)
from shared.db_schema.arena import arena_user_throttle_hashes, arena_users
from shared.services.auth_lockout_admin import (
    ActiveLockout,
    LockoutStoreClient,
    LockoutStoreUnavailableError,
    LockoutSubject,
    account_identifier_hashes,
    describe_lockouts,
    list_active_lockouts,
)
from shared.services.email_validation import EmailValidationService

__all__ = [
    "ARENA_LOCKOUT_MODULES",
    "BlockedSubject",
    "LockoutOverview",
    "ResolvedIdentifier",
    "describe_user_lockouts",
    "hashes_for_user",
    "identifiers_for_user",
    "list_lockout_overview",
    "resolve_identifier",
    "subject_for_hashes",
    "subject_for_ip",
]

ARENA_LOCKOUT_MODULES: tuple[str, ...] = ("arena",)
"""The only key module an Arena administrator may clear."""

_HASH_QUERY_BATCH_SIZE = 1_000


@dataclass(frozen=True, slots=True)
class ResolvedIdentifier:
    """What a typed identifier turned out to name."""

    user: ArenaUser | None
    hashes: frozenset[str]


@dataclass(frozen=True, slots=True)
class BlockedSubject:
    """One address or registered user with one or more active locks."""

    value: str
    lockouts: tuple[ActiveLockout, ...]

    @property
    def actions(self) -> tuple[str, ...]:
        """Return the distinct Arena actions locking this subject."""
        return tuple(sorted({lock.action for lock in self.lockouts}))

    @property
    def retry_after_seconds(self) -> int:
        """Return the longest remaining lock time for this subject."""
        return max(lock.retry_after_seconds for lock in self.lockouts)


@dataclass(frozen=True, slots=True)
class LockoutOverview:
    """Every active Arena address and resolvable registered-user lockout."""

    active_lockouts: tuple[ActiveLockout, ...]
    addresses: tuple[BlockedSubject, ...]
    users: tuple[BlockedSubject, ...]
    unresolved_identifier_count: int

    def for_subject(self, subject: LockoutSubject) -> list[ActiveLockout]:
        """Return overview rows belonging to ``subject``."""
        return [
            lock
            for lock in self.active_lockouts
            if (lock.scope == "ip" and lock.subject == subject.ip)
            or (lock.scope == "acct" and lock.subject in subject.identifier_hashes)
        ]


def identifiers_for_user(user: ArenaUser) -> list[str | None]:
    """Every raw identifier Arena's throttle buckets may have hashed for ``user``."""
    return identifiers_for_identity(user.id, user.email_normalizado, user.email_canonical)


def hashes_for_user(user: ArenaUser) -> frozenset[str]:
    """Throttle hashes of every identifier in :func:`identifiers_for_user`."""
    return hashes_for_identity(
        user.id,
        user.email_normalizado,
        user.email_canonical,
        secret=settings.JWT_SECRET_KEY,
    )


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


def _group_subjects(lockouts: list[ActiveLockout]) -> tuple[BlockedSubject, ...]:
    """Group lockout buckets by their display value."""
    grouped: dict[str, list[ActiveLockout]] = {}
    for lock in lockouts:
        grouped.setdefault(lock.subject, []).append(lock)
    return tuple(
        BlockedSubject(value=value, lockouts=tuple(grouped[value])) for value in sorted(grouped, key=str.casefold)
    )


async def list_lockout_overview(store: LockoutStoreClient | None, session: AsyncSession) -> LockoutOverview | None:
    """List active Arena lockouts and resolve account hashes to registered users.

    Account hashes are non-reversible. The startup-maintained forward index
    turns the live hash set into bounded indexed lookups, so this request never
    scans or re-hashes the Arena user table. A legitimate hash shared by more
    than one user resolves to every matching account in stable user-id order.
    """
    try:
        active = await list_active_lockouts(store, modules=ARENA_LOCKOUT_MODULES)
    except LockoutStoreUnavailableError:
        return None

    address_locks = [lock for lock in active if lock.scope == "ip" and lock.subject != "unknown"]
    account_locks_by_hash: dict[str, list[ActiveLockout]] = {}
    for lock in active:
        if lock.scope == "acct":
            account_locks_by_hash.setdefault(lock.subject, []).append(lock)

    user_locks: dict[str, list[ActiveLockout]] = {}
    resolved_hashes: set[str] = set()
    locked_hashes = sorted(account_locks_by_hash)
    secret_version = current_secret_version_id()
    for offset in range(0, len(locked_hashes), _HASH_QUERY_BATCH_SIZE):
        hash_batch = locked_hashes[offset : offset + _HASH_QUERY_BATCH_SIZE]
        rows = await session.execute(
            select(
                arena_users.c.id,
                arena_users.c.email_normalizado,
                arena_user_throttle_hashes.c.identifier_hash,
            )
            .select_from(
                arena_users.join(
                    arena_user_throttle_hashes,
                    arena_user_throttle_hashes.c.arena_user_id == arena_users.c.id,
                )
            )
            .where(
                arena_user_throttle_hashes.c.secret_version_id == secret_version,
                arena_user_throttle_hashes.c.identifier_hash.in_(hash_batch),
            )
            .order_by(arena_users.c.id, arena_user_throttle_hashes.c.identifier_hash)
        )
        for _user_id, email, identifier_hash in rows:
            user_locks.setdefault(email, []).extend(account_locks_by_hash[identifier_hash])
            resolved_hashes.add(identifier_hash)

    users = tuple(
        BlockedSubject(value=email, lockouts=tuple(user_locks[email])) for email in sorted(user_locks, key=str.casefold)
    )
    return LockoutOverview(
        active_lockouts=tuple(active),
        addresses=_group_subjects(address_locks),
        users=users,
        unresolved_identifier_count=len(account_locks_by_hash.keys() - resolved_hashes),
    )
