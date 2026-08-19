#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Container healthcheck entrypoint for the rating worker."""

from __future__ import annotations

from pathlib import Path

from shared.services.worker_heartbeat import heartbeat_is_fresh


def heartbeat_is_healthy(*, now: float | None = None) -> bool:
    """Return whether the rating worker heartbeat file is present and fresh.

    Loading the settings is part of the probe on purpose: a configuration the
    worker itself could not load is an unhealthy container. Any failure --
    including an unloadable configuration -- is reported as unhealthy rather
    than raised.

    Args:
        now: Optional POSIX timestamp override for deterministic callers/tests.

    Returns:
        ``True`` when the heartbeat file exists and its age is not greater than
        the configured staleness threshold, otherwise ``False``.
    """
    try:
        from rating.config import settings

        return heartbeat_is_fresh(
            Path(settings.RATING_HEARTBEAT_FILE),
            max_age_seconds=settings.RATING_HEARTBEAT_STALE_SECONDS,
            now=now,
        )
    except Exception:
        return False


def main() -> int:
    """Run the container healthcheck process.

    Returns:
        Process exit code expected by Docker healthchecks.
    """
    return 0 if heartbeat_is_healthy() else 1


if __name__ == "__main__":
    raise SystemExit(main())
