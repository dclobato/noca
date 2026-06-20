#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Worker presence registration and dashboard queries backed by Valkey."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast

WORKER_PRESENCE_PREFIX = "noca:worker-presence"

_HEARTBEAT_SCRIPT = """
redis.call("HSET", KEYS[1], ARGV[1], ARGV[2])
redis.call("SET", KEYS[2], ARGV[3], "EX", ARGV[4])
return 1
"""

_REMOVE_SCRIPT = """
redis.call("HDEL", KEYS[1], ARGV[1])
redis.call("DEL", KEYS[2])
redis.call("HDEL", KEYS[3], ARGV[1])
return 1
"""


class WorkerClass(StrEnum):
    """Worker process classes displayed by the Arena administration dashboard."""

    AUTOJUDGE = "autojudge"
    AIASSISTANT = "aiassistant"
    RATING = "rating"


@dataclass(frozen=True, slots=True)
class WorkerPresence:
    """Dashboard representation of one previously seen worker."""

    worker_class: WorkerClass
    worker_id: str
    started_at: datetime
    last_seen_at: datetime
    online: bool
    last_job_at: datetime | None = None


WorkerPresenceClient = Any


def resolve_worker_id(configured_worker_id: str) -> str:
    """Return a configured worker ID or a process-unique host fallback.

    Args:
        configured_worker_id: Optional ID supplied by worker configuration.

    Returns:
        The stripped configured ID, or ``<fqdn>:<pid>`` when it is empty.
    """
    resolved = configured_worker_id.strip()
    return resolved or f"{socket.getfqdn()}:{os.getpid()}"


def worker_registry_key(worker_class: WorkerClass) -> str:
    """Return the durable registry hash key for a worker class."""
    return f"{WORKER_PRESENCE_PREFIX}:{worker_class.value}:seen"


def worker_live_key(worker_class: WorkerClass, worker_id: str) -> str:
    """Return the expiring live-presence key for one worker."""
    return f"{WORKER_PRESENCE_PREFIX}:{worker_class.value}:live:{worker_id}"


def worker_last_jobs_key(worker_class: WorkerClass) -> str:
    """Return the durable last-job hash key for a worker class."""
    return f"{WORKER_PRESENCE_PREFIX}:{worker_class.value}:last-jobs"


async def publish_worker_presence(
    client: WorkerPresenceClient,
    *,
    worker_class: WorkerClass,
    worker_id: str,
    started_at: datetime,
    ttl_seconds: int,
    observed_at: datetime | None = None,
) -> None:
    """Atomically register a worker and refresh its expiring live marker.

    Args:
        client: Connected raw Valkey client or ``ValkeyRuntime``.
        worker_class: Worker process class.
        worker_id: Stable worker identifier.
        started_at: Start time for the current process instance.
        ttl_seconds: Expiry for the live marker.
        observed_at: Heartbeat time. Defaults to the current UTC time.
    """
    started_text = started_at.astimezone(UTC).isoformat()
    last_seen_text = (observed_at or datetime.now(UTC)).astimezone(UTC).isoformat()
    payload = json.dumps(
        {
            "worker_id": worker_id,
            "started_at": started_text,
            "last_seen_at": last_seen_text,
        },
        separators=(",", ":"),
    )
    await client.eval(
        _HEARTBEAT_SCRIPT,
        2,
        worker_registry_key(worker_class),
        worker_live_key(worker_class, worker_id),
        worker_id,
        payload,
        payload,
        str(ttl_seconds),
    )


async def publish_worker_last_job(
    client: WorkerPresenceClient,
    *,
    worker_class: WorkerClass,
    worker_id: str,
    started_at: datetime | None = None,
) -> None:
    """Record the start time of the most recent job for one worker instance.

    Args:
        client: Connected raw Valkey client or ``ValkeyRuntime``.
        worker_class: Worker process class.
        worker_id: Stable worker identifier.
        started_at: Job start time. Defaults to the current UTC time.
    """
    value = (started_at or datetime.now(UTC)).astimezone(UTC).isoformat()
    await client.hset(worker_last_jobs_key(worker_class), worker_id, value)


async def remove_worker(
    client: WorkerPresenceClient,
    *,
    worker_class: WorkerClass,
    worker_id: str,
) -> None:
    """Remove one worker from the dashboard until its next heartbeat."""
    await client.eval(
        _REMOVE_SCRIPT,
        3,
        worker_registry_key(worker_class),
        worker_live_key(worker_class, worker_id),
        worker_last_jobs_key(worker_class),
        worker_id,
    )


async def mark_worker_offline(
    client: WorkerPresenceClient,
    *,
    worker_class: WorkerClass,
    worker_id: str,
) -> None:
    """Delete a live marker while retaining the durable seen-worker record."""
    await client.delete(worker_live_key(worker_class, worker_id))


async def list_workers(
    client: WorkerPresenceClient,
    worker_class: WorkerClass,
) -> list[WorkerPresence]:
    """Return all seen workers of a class, with current online status."""
    raw_registry = await client.hgetall(worker_registry_key(worker_class))
    if not raw_registry:
        return []

    registry = {_decode(field): _parse_registry_value(_decode(value)) for field, value in raw_registry.items()}
    worker_ids = sorted(registry)
    live_values = await client.mget([worker_live_key(worker_class, worker_id) for worker_id in worker_ids])
    if live_values is None:
        live_values = [None] * len(worker_ids)

    raw_last_jobs = await client.hmget(worker_last_jobs_key(worker_class), worker_ids)
    if raw_last_jobs is None:
        raw_last_jobs = [None] * len(worker_ids)

    workers = [
        WorkerPresence(
            worker_class=worker_class,
            worker_id=worker_id,
            started_at=registry[worker_id][0],
            last_seen_at=registry[worker_id][1],
            online=live_value is not None,
            last_job_at=_parse_last_job(last_job_raw),
        )
        for worker_id, live_value, last_job_raw in zip(worker_ids, live_values, raw_last_jobs, strict=True)
    ]
    return sorted(workers, key=lambda worker: (not worker.online, worker.worker_id.casefold()))


async def list_all_workers(
    client: WorkerPresenceClient,
) -> dict[WorkerClass, list[WorkerPresence]]:
    """Return dashboard worker rows grouped by worker class."""
    rows = await asyncio.gather(*(list_workers(client, worker_class) for worker_class in WorkerClass))
    return dict(zip(WorkerClass, rows, strict=True))


async def worker_presence_loop(
    client: WorkerPresenceClient,
    *,
    worker_class: WorkerClass,
    worker_id: str,
    started_at: datetime,
    interval_seconds: float,
    ttl_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    """Publish immediately and then periodically until shutdown is requested."""
    while not stop_event.is_set():
        await publish_worker_presence(
            client,
            worker_class=worker_class,
            worker_id=worker_id,
            started_at=started_at,
            ttl_seconds=ttl_seconds,
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


def _decode(value: object) -> str:
    """Decode raw Valkey bytes while preserving string clients."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return cast(str, value)


def _parse_last_job(value: str | None) -> datetime | None:
    """Parse a last-job ISO timestamp from Valkey, returning None when absent or malformed."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError, TypeError:
        return None


def _parse_registry_value(value: str) -> tuple[datetime, datetime]:
    """Parse current JSON records and legacy start-timestamp-only records."""
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        started_at = datetime.fromisoformat(value)
        return started_at, started_at
    started_at = datetime.fromisoformat(payload["started_at"])
    last_seen_at = datetime.fromisoformat(payload.get("last_seen_at", payload["started_at"]))
    return started_at, last_seen_at
