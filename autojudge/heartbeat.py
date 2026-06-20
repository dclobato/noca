#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""
Worker process identity and heartbeat file management.

The heartbeat file is touched on a configured interval so that external
process managers can detect stale or crashed workers.
"""

import asyncio
from contextlib import suppress
from pathlib import Path

from autojudge.config import settings
from autojudge.worker_identity import worker_id as _worker_id


def worker_id() -> str:
    """
    Return the stable identifier for this worker process.

    This compatibility wrapper preserves historical imports from
    ``autojudge.heartbeat`` while delegating the implementation to
    ``autojudge.worker_identity``.

    Returns:
        String identifier for this worker.
    """
    return _worker_id()


def heartbeat_path() -> Path:
    """Return the configured heartbeat file path."""
    return Path(settings.HEARTBEAT_FILE)


def touch_heartbeat_file() -> None:
    """Create or update the modification time of the heartbeat file."""
    path = heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def remove_heartbeat_file() -> None:
    """Remove the heartbeat file, ignoring errors if it does not exist."""
    with suppress(FileNotFoundError, PermissionError):
        heartbeat_path().unlink()


async def heartbeat_loop(shutdown_event: asyncio.Event) -> None:
    """
    Async loop that updates the heartbeat file until shutdown is requested.

    Args:
        shutdown_event: Event that signals the loop to stop.
    """
    while not shutdown_event.is_set():
        await asyncio.to_thread(touch_heartbeat_file)
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=settings.HEARTBEAT_INTERVAL_S)
        except TimeoutError:
            continue
