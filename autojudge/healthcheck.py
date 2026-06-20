#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Container healthcheck helpers for the autojudge worker."""

from __future__ import annotations

import time
from pathlib import Path

from autojudge.config import settings


def heartbeat_is_healthy(*, now: float | None = None) -> bool:
    """Return whether the worker heartbeat file exists and is fresh enough.

    Args:
        now: Optional POSIX timestamp override for deterministic callers/tests.

    Returns:
        ``True`` when the heartbeat file exists and its age is not greater than
        the configured staleness threshold, otherwise ``False``.
    """
    heartbeat = Path(settings.HEARTBEAT_FILE)
    if not heartbeat.exists():
        return False

    current_time = time.time() if now is None else now
    age = current_time - heartbeat.stat().st_mtime
    return age <= settings.HEARTBEAT_STALE_THRESHOLD_S


def main() -> int:
    """Run the container healthcheck process.

    Returns:
        Process exit code expected by Docker healthchecks.
    """
    return 0 if heartbeat_is_healthy() else 1


if __name__ == "__main__":
    raise SystemExit(main())
