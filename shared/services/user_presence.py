#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Online-presence tracking for end users, backed by Valkey.

This is a thin, best-effort layer modelled on
:mod:`shared.services.valkey_service.worker_presence`. Each heartbeat updates two
structures atomically:

* a per-user **live key** with a short TTL, whose existence means "online" — read
  in batch via ``mget`` so a page that renders many avatars never causes one
  Valkey round-trip per avatar; and
* a per-domain **online sorted set** (member = user id, score = last-seen epoch
  seconds) used to count distinct online users without enumerating keys.

The service is identity-domain aware (``arena`` now, ``contest`` later) so the
Web module can reuse it without colliding with Arena keys.  All functions accept
either a :class:`~shared.services.valkey_service.runtime.ValkeyRuntime` or a raw
``valkey.asyncio.Valkey`` client and never raise on a Valkey outage: writes are
dropped silently, reads degrade to "everyone offline", and the count returns
``None`` (kept distinct from a genuine count of ``0``).
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

PRESENCE_PREFIX = "noca:user-presence"

#: Upper bound on the number of ids honoured by a single batch read, guarding
#: against an oversized ``mget`` triggered by a hostile or buggy client.
MAX_PRESENCE_BATCH = 500

# Atomically refresh the live key (with TTL) and the online sorted set.
# KEYS[1]=live key, KEYS[2]=online set; ARGV[1]=ttl, ARGV[2]=score, ARGV[3]=user id.
_MARK_ONLINE_SCRIPT = """
redis.call('SET', KEYS[1], '1', 'EX', ARGV[1])
redis.call('ZADD', KEYS[2], ARGV[2], ARGV[3])
return 1
"""

# Atomically drop the live key and remove the member from the online set.
# KEYS[1]=live key, KEYS[2]=online set; ARGV[1]=user id.
_MARK_OFFLINE_SCRIPT = """
redis.call('DEL', KEYS[1])
redis.call('ZREM', KEYS[2], ARGV[1])
return 1
"""

# Purge stale members (score <= cutoff) then return the live cardinality.
# KEYS[1]=online set; ARGV[1]=cutoff epoch seconds.
_COUNT_SCRIPT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
return redis.call('ZCARD', KEYS[1])
"""


def user_live_key(domain: str, user_id: str) -> str:
    """Return the expiring live-presence key for one user.

    Args:
        domain: Identity domain namespace (e.g. ``"arena"`` or ``"contest"``).
        user_id: Stable user identifier.

    Returns:
        The Valkey key whose existence denotes the user being online.
    """
    return f"{PRESENCE_PREFIX}:{domain}:live:{user_id}"


def online_set_key(domain: str) -> str:
    """Return the sorted-set key tracking all online users in a domain.

    Args:
        domain: Identity domain namespace (e.g. ``"arena"`` or ``"contest"``).

    Returns:
        The sorted-set key whose members are currently-online user ids.
    """
    return f"{PRESENCE_PREFIX}:{domain}:online"


async def mark_user_online(
    client: Any,
    *,
    domain: str,
    user_id: str,
    ttl_seconds: int,
) -> None:
    """Refresh a user's presence, keeping them online for ``ttl_seconds``.

    Atomically refreshes the live key and the online sorted set. Best-effort: a
    Valkey outage is swallowed, so callers can fire this from a hot request path
    without guarding it.

    Args:
        client: Connected raw Valkey client or ``ValkeyRuntime``.
        domain: Identity domain namespace.
        user_id: Stable user identifier.
        ttl_seconds: Expiry of the live marker, in seconds.
    """
    try:
        await client.eval(
            _MARK_ONLINE_SCRIPT,
            2,
            user_live_key(domain, user_id),
            online_set_key(domain),
            str(max(1, ttl_seconds)),
            str(int(time.time())),
            user_id,
        )
    except Exception as exc:
        # Never raise: a Valkey outage must not break the request marking online.
        logger.warning(f"Presence mark_user_online failed for {domain}/{user_id}: {str(exc)}")


async def mark_user_offline(
    client: Any,
    *,
    domain: str,
    user_id: str,
) -> None:
    """Immediately clear a user's presence (e.g. on explicit logout).

    Args:
        client: Connected raw Valkey client or ``ValkeyRuntime``.
        domain: Identity domain namespace.
        user_id: Stable user identifier.
    """
    try:
        await client.eval(
            _MARK_OFFLINE_SCRIPT,
            2,
            user_live_key(domain, user_id),
            online_set_key(domain),
            user_id,
        )
    except Exception as exc:
        logger.warning(f"Presence mark_user_offline failed for {domain}/{user_id}: {str(exc)}")


async def count_online_users(
    client: Any,
    *,
    domain: str,
    ttl_seconds: int,
) -> int | None:
    """Return the number of distinct users seen within the last ``ttl_seconds``.

    Purges members whose last-seen score has aged past the TTL, then returns the
    cardinality. Best-effort, but unavailability is reported as ``None`` so the
    caller can keep "no users online" (``0``) distinct from "count unknown".

    Args:
        client: Connected raw Valkey client or ``ValkeyRuntime``.
        domain: Identity domain namespace.
        ttl_seconds: Freshness window; mirrors the live-marker TTL.

    Returns:
        The count of currently-online users, or ``None`` when unavailable.
    """
    cutoff = int(time.time()) - max(1, ttl_seconds)
    try:
        result = await client.eval(_COUNT_SCRIPT, 1, online_set_key(domain), str(cutoff))
    except Exception as exc:
        logger.warning(f"Presence count_online_users failed for domain {domain}: {str(exc)}")
        return None
    if result is None:
        return None
    try:
        return int(result)
    except TypeError, ValueError:
        return None


async def get_users_online_map(
    client: Any,
    *,
    domain: str,
    user_ids: list[str],
) -> dict[str, bool]:
    """Return a ``{user_id: online}`` map for the given users in one round-trip.

    Duplicate and blank ids are removed before the lookup; at most
    :data:`MAX_PRESENCE_BATCH` distinct ids are queried.  On Valkey
    unavailability every requested id is reported offline.

    Args:
        client: Connected raw Valkey client or ``ValkeyRuntime``.
        domain: Identity domain namespace.
        user_ids: User identifiers to check.

    Returns:
        Mapping from each requested id to its current online state.
    """
    unique_ids: list[str] = []
    seen: set[str] = set()
    for raw_id in user_ids:
        candidate = (raw_id or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        unique_ids.append(candidate)
        if len(unique_ids) >= MAX_PRESENCE_BATCH:
            break

    if not unique_ids:
        return {}

    try:
        values = await client.mget([user_live_key(domain, user_id) for user_id in unique_ids])
    except Exception as exc:
        # Never raise: degrade to "everyone offline" when Valkey is unreachable.
        logger.warning(f"Presence get_users_online_map failed for domain {domain}: {str(exc)}")
        return {user_id: False for user_id in unique_ids}

    if values is None:
        return {user_id: False for user_id in unique_ids}

    return {user_id: value is not None for user_id, value in zip(unique_ids, values, strict=True)}
