#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Seed and inspect throttle locks for the Arena lockout route tests.

Keys are derived with the real ``hash_identifier`` and Arena's configured
secret, so a test proves the unlock against the exact keys the login routes
write. Underscore-prefixed so pytest does not collect it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arena.config import settings
from shared.db_schema import security_events
from shared.services.auth_rate_limit import hash_identifier
from tests.shared._auth_fake_valkey import AuthFakeValkey

LOCK_SECONDS = 600


def lock_key(*, module: str, action: str, identifier: str | None = None, ip: str | None = None) -> str:
    """The lock key of one bucket, for an account identifier or an IP."""
    if identifier is not None:
        digest = hash_identifier(identifier, secret=settings.JWT_SECRET_KEY)
        return f"auth:rate-limit:{module}:{action}:acct:{digest}:lock"
    assert ip is not None
    return f"auth:rate-limit:{module}:{action}:ip:{ip}:lock"


def seed_lock(valkey: AuthFakeValkey, **bucket: Any) -> str:
    """Put one live lock (and its failure counter) into the fake store; return the lock key."""
    key = lock_key(**bucket)
    valkey.locks[key] = valkey.clock + LOCK_SECONDS
    valkey.counts[key.removesuffix(":lock") + ":failures"] = (5, valkey.clock + 900)
    return key


def is_locked(valkey: AuthFakeValkey, **bucket: Any) -> bool:
    """Whether the bucket's lock is still live in the fake store."""
    return lock_key(**bucket) in valkey.live_keys()


async def admin_actions(session: AsyncSession, action: str) -> list[dict[str, Any]]:
    """Metadata of every Arena ``admin_action`` audit row with the given action."""
    result = await session.execute(
        select(security_events.c.metadata)
        .where(security_events.c.module == "arena", security_events.c.event_type == "admin_action")
        .order_by(security_events.c.id)
    )
    return [dict(payload or {}) for payload in result.scalars() if (payload or {}).get("action") == action]
