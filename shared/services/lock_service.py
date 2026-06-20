#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Valkey-backed TTL locks for contest workflows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

import valkey.asyncio as aivalkey

from shared.services.valkey_service import ValkeyRuntime

LockKind = Literal["clarification", "task", "review"]
LockClient = aivalkey.Valkey | ValkeyRuntime

_RELEASE_IF_OWNER_SCRIPT = """
local payload = redis.call('GET', KEYS[1])
if not payload then
    return 0
end
local decoded = cjson.decode(payload)
if decoded['holder_id'] ~= ARGV[1] then
    return 0
end
return redis.call('DEL', KEYS[1])
"""


@dataclass(frozen=True, slots=True)
class LockState:
    """Current lock state for one resource."""

    kind: LockKind
    contest_id: str
    resource_id: str
    holder_id: str
    holder_role: str
    acquired_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class LockBatchResult:
    """Bulk lock lookup result with availability metadata."""

    service_available: bool
    locks_by_resource_id: dict[str, LockState]


def lock_key(kind: LockKind, contest_id: str, resource_id: str) -> str:
    """Build the canonical Valkey key for one lock."""
    return f"lock:{kind}:{contest_id}:{resource_id}"


def _serialize_lock(lock: LockState) -> str:
    return json.dumps(
        {
            "kind": lock.kind,
            "contest_id": lock.contest_id,
            "resource_id": lock.resource_id,
            "holder_id": lock.holder_id,
            "holder_role": lock.holder_role,
            "acquired_at": lock.acquired_at.astimezone(UTC).isoformat(),
            "expires_at": lock.expires_at.astimezone(UTC).isoformat(),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _deserialize_lock(payload: str) -> LockState:
    decoded = cast(dict[str, str], json.loads(payload))
    return LockState(
        kind=cast(LockKind, decoded["kind"]),
        contest_id=decoded["contest_id"],
        resource_id=decoded["resource_id"],
        holder_id=decoded["holder_id"],
        holder_role=decoded["holder_role"],
        acquired_at=datetime.fromisoformat(decoded["acquired_at"]),
        expires_at=datetime.fromisoformat(decoded["expires_at"]),
    )


def _runtime_available(client_or_runtime: LockClient) -> bool:
    if isinstance(client_or_runtime, ValkeyRuntime):
        return client_or_runtime.is_available
    return True


async def acquire_lock(
    client_or_runtime: LockClient,
    *,
    kind: LockKind,
    contest_id: str,
    resource_id: str,
    holder_id: str,
    holder_role: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> bool | None:
    """Try to acquire a TTL-backed lock.

    Returns True on success, False if already held, and None if the lock
    service is unavailable.
    """
    if not _runtime_available(client_or_runtime):
        return None

    acquired_at = (now or datetime.now(UTC)).astimezone(UTC)
    expires_at = acquired_at + timedelta(seconds=max(1, ttl_seconds))
    key = lock_key(kind, contest_id, resource_id)
    payload = _serialize_lock(
        LockState(
            kind=kind,
            contest_id=contest_id,
            resource_id=resource_id,
            holder_id=holder_id,
            holder_role=holder_role,
            acquired_at=acquired_at,
            expires_at=expires_at,
        )
    )

    if isinstance(client_or_runtime, ValkeyRuntime):
        return await client_or_runtime.set_if_absent(key, payload, ex=max(1, ttl_seconds))

    result = await client_or_runtime.set(key, payload, ex=max(1, ttl_seconds), nx=True)
    if isinstance(result, bytes):
        result = result.decode()
    return bool(result)


async def get_lock(
    client_or_runtime: LockClient,
    *,
    kind: LockKind,
    contest_id: str,
    resource_id: str,
) -> LockState | None:
    """Fetch one lock if available and present."""
    if not _runtime_available(client_or_runtime):
        return None

    key = lock_key(kind, contest_id, resource_id)
    payload = await client_or_runtime.get(key)
    if payload is None:
        return None
    return _deserialize_lock(payload)


async def get_locks(
    client_or_runtime: LockClient,
    *,
    kind: LockKind,
    contest_id: str,
    resource_ids: list[str],
) -> LockBatchResult:
    """Fetch many locks for a contest-scoped resource list."""
    if not resource_ids or not _runtime_available(client_or_runtime):
        return LockBatchResult(service_available=_runtime_available(client_or_runtime), locks_by_resource_id={})

    keys = [lock_key(kind, contest_id, resource_id) for resource_id in resource_ids]
    if isinstance(client_or_runtime, ValkeyRuntime):
        payloads = await client_or_runtime.mget(keys)
        if payloads is None:
            return LockBatchResult(service_available=False, locks_by_resource_id={})
    else:
        raw_payloads = await client_or_runtime.mget(keys)
        payloads = [str(payload) if payload is not None else None for payload in raw_payloads]

    locks_by_resource_id: dict[str, LockState] = {}
    for resource_id, payload in zip(resource_ids, payloads, strict=False):
        if payload is None:
            continue
        locks_by_resource_id[resource_id] = _deserialize_lock(payload)
    return LockBatchResult(service_available=True, locks_by_resource_id=locks_by_resource_id)


async def release_lock(
    client_or_runtime: LockClient,
    *,
    kind: LockKind,
    contest_id: str,
    resource_id: str,
    holder_id: str,
) -> bool | None:
    """Release one lock only when the holder matches."""
    if not _runtime_available(client_or_runtime):
        return None

    key = lock_key(kind, contest_id, resource_id)
    if isinstance(client_or_runtime, ValkeyRuntime):
        result = await client_or_runtime.eval(_RELEASE_IF_OWNER_SCRIPT, 1, key, holder_id)
    else:
        result = await cast(Any, client_or_runtime.eval(_RELEASE_IF_OWNER_SCRIPT, 1, key, holder_id))
    if result is None:
        return None
    return bool(result)


async def force_release_lock(
    client_or_runtime: LockClient,
    *,
    kind: LockKind,
    contest_id: str,
    resource_id: str,
) -> bool | None:
    """Release one lock without checking the holder."""
    if not _runtime_available(client_or_runtime):
        return None

    key = lock_key(kind, contest_id, resource_id)
    if isinstance(client_or_runtime, ValkeyRuntime):
        await client_or_runtime.delete(key)
        return True

    deleted = await client_or_runtime.delete(key)
    return bool(deleted)
