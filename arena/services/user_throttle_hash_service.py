#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Maintain the forward index for Arena authentication-throttle hashes.

Throttle account keys contain an HMAC rather than clear identifiers. The
dashboard resolves those hashes through a materialized mapping instead of
recomputing every candidate for every user during a request. New registrations
refresh one user inside their existing transaction.

Which secret derived a hash is part of its identity, so every mapping row
points at a row in ``arena_throttle_secret_versions`` -- one per distinct
``JWT_SECRET_KEY`` -- through a 4-byte integer instead of repeating the
64-character fingerprint on each row.

Startup reconciles the index, but does the work only when it is actually
needed. Rebuilding unconditionally rewrote every row on every boot, which made
readiness scale with the user count and produced a table's worth of dead tuples
per restart. The gate asks whether any user is missing from the current
generation and rebuilds only on a backfill, a secret rotation, or a genuine
gap; each user's rows are complete or absent, never partial, because a rebuild
commits once.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arena.config import settings
from arena.models.arena_users import ArenaUser
from shared.db_schema.arena import arena_throttle_secret_versions, arena_user_throttle_hashes, arena_users
from shared.services.auth_lockout_admin import account_identifier_hashes

__all__ = [
    "current_secret_version_id",
    "hashes_for_identity",
    "identifiers_for_identity",
    "rebuild_user_throttle_hash_index",
    "refresh_user_throttle_hashes",
    "throttle_secret_fingerprint",
]

logger = logging.getLogger(__name__)

_REBUILD_BATCH_SIZE = 1_000
_PRIMARY_KEY_COLUMNS = ("secret_version_id", "identifier_hash", "arena_user_id")
Identity = tuple[str, str, str | None]


def identifiers_for_identity(user_id: str, email: str, canonical_email: str | None) -> list[str | None]:
    """Return every throttle identifier derived from minimal user identity fields."""
    return [email, canonical_email, user_id, f"sub:{user_id}", f"sub:{email}"]


def hashes_for_identity(
    user_id: str,
    email: str,
    canonical_email: str | None,
    *,
    secret: str,
) -> frozenset[str]:
    """Return the throttle hashes for one Arena user's complete identifier recipe."""
    return account_identifier_hashes(
        identifiers_for_identity(user_id, email, canonical_email),
        secret=secret,
    )


def throttle_secret_fingerprint(secret: str) -> str:
    """Return a non-secret version marker for one throttle HMAC key."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def current_secret_version_id(secret: str | None = None) -> Any:
    """Return a scalar subquery selecting the generation id of the active secret.

    Readers embed this rather than resolving the id first, so one indexed
    lookup on the unique fingerprint keeps the equality on the leading primary
    key column of :data:`arena_user_throttle_hashes`. It selects nothing when
    the secret has never been indexed, which correctly matches no rows.
    """
    fingerprint = throttle_secret_fingerprint(secret if secret is not None else settings.JWT_SECRET_KEY)
    return (
        select(arena_throttle_secret_versions.c.id)
        .where(arena_throttle_secret_versions.c.secret_fingerprint == fingerprint)
        .scalar_subquery()
    )


def _dialect_name(session: AsyncSession) -> str:
    """Return the database dialect backing ``session``."""
    return session.get_bind().dialect.name


def _insert_ignoring_conflicts(session: AsyncSession, values: list[dict[str, Any]]) -> Any:
    """Build the dialect-specific idempotent insert used by startup replicas."""
    if _dialect_name(session) == "postgresql":
        return (
            postgresql_insert(arena_user_throttle_hashes)
            .values(values)
            .on_conflict_do_nothing(index_elements=list(_PRIMARY_KEY_COLUMNS))
        )
    return (
        sqlite_insert(arena_user_throttle_hashes)
        .values(values)
        .on_conflict_do_nothing(index_elements=list(_PRIMARY_KEY_COLUMNS))
    )


def _insert_version_ignoring_conflicts(session: AsyncSession, fingerprint: str) -> Any:
    """Build the dialect-specific idempotent insert of one secret generation."""
    values = {"secret_fingerprint": fingerprint, "dta_criacao": func.now()}
    if _dialect_name(session) == "postgresql":
        return (
            postgresql_insert(arena_throttle_secret_versions)
            .values(values)
            .on_conflict_do_nothing(index_elements=["secret_fingerprint"])
        )
    return (
        sqlite_insert(arena_throttle_secret_versions)
        .values(values)
        .on_conflict_do_nothing(index_elements=["secret_fingerprint"])
    )


def _index_values(identities: Sequence[Identity], *, secret: str, version_id: int) -> list[dict[str, Any]]:
    """Build database rows for a bounded identity batch."""
    return [
        {
            "secret_version_id": version_id,
            "identifier_hash": identifier_hash,
            "arena_user_id": user_id,
        }
        for user_id, email, canonical_email in identities
        for identifier_hash in sorted(hashes_for_identity(user_id, email, canonical_email, secret=secret))
    ]


async def _ensure_secret_version(session: AsyncSession, secret: str) -> int:
    """Return the generation id of ``secret``, creating its row when new.

    Concurrent Arena replicas may reach this at the same time, so the insert
    ignores a unique-constraint conflict and the id is read back afterwards.
    """
    fingerprint = throttle_secret_fingerprint(secret)
    select_id = select(arena_throttle_secret_versions.c.id).where(
        arena_throttle_secret_versions.c.secret_fingerprint == fingerprint
    )
    version_id = await session.scalar(select_id)
    if version_id is not None:
        return int(version_id)

    await session.execute(_insert_version_ignoring_conflicts(session, fingerprint))
    version_id = await session.scalar(select_id)
    if version_id is None:  # pragma: no cover - only reachable if the row vanishes mid-transaction
        raise RuntimeError("Could not allocate a throttle-secret generation id.")
    return int(version_id)


async def refresh_user_throttle_hashes(session: AsyncSession, user: ArenaUser) -> None:
    """Replace the forward-index rows for one persisted user in the caller's transaction."""
    secret = settings.JWT_SECRET_KEY
    version_id = await _ensure_secret_version(session, secret)
    identity = (user.id, user.email_normalizado, user.email_canonical)
    values = _index_values([identity], secret=secret, version_id=version_id)
    await session.execute(
        delete(arena_user_throttle_hashes).where(
            arena_user_throttle_hashes.c.arena_user_id == user.id,
            arena_user_throttle_hashes.c.secret_version_id == version_id,
        )
    )
    await session.execute(_insert_ignoring_conflicts(session, values))


async def _index_is_current(session: AsyncSession, version_id: int) -> bool:
    """Whether ``version_id`` already covers every registered Arena user.

    This asks directly whether any user is missing, rather than comparing
    counts, so it needs no assumption about how many hashes a user yields and
    it stops at the first uncovered row. Each probe is an index lookup on
    ``ix_arena_user_throttle_hashes_user_version``. Deletions cascade, and a
    crashed rebuild leaves no partial state to mistake for coverage because a
    rebuild commits exactly once.
    """
    uncovered = (
        select(arena_users.c.id)
        .where(
            ~select(1)
            .select_from(arena_user_throttle_hashes)
            .where(
                arena_user_throttle_hashes.c.arena_user_id == arena_users.c.id,
                arena_user_throttle_hashes.c.secret_version_id == version_id,
            )
            .exists()
        )
        .limit(1)
    )
    return await session.scalar(select(~uncovered.exists())) is True


async def rebuild_user_throttle_hash_index(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reconcile the forward index at startup, doing the work only when needed.

    The gate costs one indexed anti-join on a boot that changes nothing. When a
    rebuild is required, user identities are streamed in bounded batches,
    hashing runs in a worker thread, and the single final commit exposes the
    complete generation at once; obsolete generations are dropped only after
    that replacement is ready, and their mapping rows follow by cascade.
    """
    secret = settings.JWT_SECRET_KEY
    indexed_users = 0
    indexed_hashes = 0

    async with session_factory() as gate_session:
        version_id = await _ensure_secret_version(gate_session, secret)
        await gate_session.commit()
        if await _index_is_current(gate_session, version_id):
            logger.info("- Arena throttle-hash index already current (generation=%d)", version_id)
            return

    async with session_factory() as read_session, session_factory() as write_session:
        result = await read_session.stream(
            select(
                arena_users.c.id,
                arena_users.c.email_normalizado,
                arena_users.c.email_canonical,
            )
            .order_by(arena_users.c.id)
            .execution_options(yield_per=_REBUILD_BATCH_SIZE)
        )
        async for partition in result.partitions(_REBUILD_BATCH_SIZE):
            identities: list[Identity] = [(str(row[0]), str(row[1]), cast(str | None, row[2])) for row in partition]
            user_ids = [identity[0] for identity in identities]
            values = await asyncio.to_thread(_index_values, identities, secret=secret, version_id=version_id)
            await write_session.execute(
                delete(arena_user_throttle_hashes).where(
                    arena_user_throttle_hashes.c.arena_user_id.in_(user_ids),
                    arena_user_throttle_hashes.c.secret_version_id == version_id,
                )
            )
            if values:
                await write_session.execute(_insert_ignoring_conflicts(write_session, values))
            indexed_users += len(identities)
            indexed_hashes += len(values)

        await write_session.execute(
            delete(arena_throttle_secret_versions).where(arena_throttle_secret_versions.c.id != version_id)
        )
        await write_session.commit()

    logger.info(
        "- Arena throttle-hash index rebuilt (generation=%d, users=%d, hashes=%d)",
        version_id,
        indexed_users,
        indexed_hashes,
    )
