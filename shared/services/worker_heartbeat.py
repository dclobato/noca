#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Local heartbeat-file helpers shared by the queue-consuming workers.

A worker process publishes Valkey worker presence for the health monitor, but a
container healthcheck cannot reuse that record: the presence key is namespaced by
worker ID, and an unconfigured worker derives its ID from ``<fqdn>:<pid>`` -- a
value the separate healthcheck process cannot reconstruct. The heartbeat file is
the container-local answer instead: the worker touches it on an interval and the
healthcheck only asks how old it is, so the probe stays independent of Valkey,
of PostgreSQL, and of the worker's identity.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from pathlib import Path


def touch_heartbeat(path: Path) -> None:
    """Create or refresh the modification time of a heartbeat file.

    Args:
        path: Heartbeat file path. Missing parent directories are created.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def remove_heartbeat(path: Path) -> None:
    """Delete a heartbeat file, ignoring an absent file or a denied unlink.

    Args:
        path: Heartbeat file path.
    """
    with suppress(FileNotFoundError, PermissionError):
        path.unlink()


def heartbeat_is_fresh(path: Path, *, max_age_seconds: float, now: float | None = None) -> bool:
    """Return whether a heartbeat file exists and is recent enough.

    Args:
        path: Heartbeat file path.
        max_age_seconds: Highest age, in seconds, still considered healthy.
        now: Optional POSIX timestamp override for deterministic callers/tests.

    Returns:
        ``True`` when the file exists and its age does not exceed
        ``max_age_seconds``; otherwise ``False``. Any filesystem error is
        reported as stale rather than raised, because a healthcheck that cannot
        read the heartbeat has not observed a healthy worker.
    """
    try:
        modified_at = path.stat().st_mtime
    except OSError:
        return False
    current_time = time.time() if now is None else now
    return current_time - modified_at <= max_age_seconds


async def heartbeat_loop(
    path: Path,
    *,
    interval_seconds: float,
    stop_event: asyncio.Event,
) -> None:
    """Refresh a heartbeat file immediately and then until shutdown is requested.

    Args:
        path: Heartbeat file path.
        interval_seconds: Seconds between refreshes.
        stop_event: Event that signals the loop to stop.
    """
    while not stop_event.is_set():
        await asyncio.to_thread(touch_heartbeat, path)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
