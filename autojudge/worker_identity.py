#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Stable worker identity helper shared across worker subsystems."""

import os
import socket

from autojudge.config import settings


def worker_id() -> str:
    """
    Return a stable identifier for this worker process.

    Uses the configured WORKER_ID if set, otherwise falls back to
    ``hostname:pid`` which is unique per host.

    Returns:
        String identifier for this worker.
    """
    wid = settings.WORKER_ID
    if wid:
        return wid
    return f"{socket.getfqdn()}:{os.getpid()}"
