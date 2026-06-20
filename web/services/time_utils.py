#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from __future__ import annotations

from datetime import datetime, timedelta


def normalize_now_for_reference(now: datetime, reference: datetime) -> datetime:
    """Normalize ``now`` timezone awareness to match ``reference``.

    This is used where values may come from SQLite (naive datetimes) while
    current time helpers return timezone-aware datetimes.

    Args:
        now: Current timestamp to normalize.
        reference: Reference timestamp that defines timezone awareness.

    Returns:
        ``now`` unchanged when ``reference`` is timezone-aware, otherwise
        a naive copy of ``now``.
    """
    if reference.tzinfo is None:
        return now.replace(tzinfo=None)
    return now


def elapsed_since(reference: datetime, *, now: datetime) -> timedelta:
    """Return elapsed wall time between ``reference`` and ``now``.

    Args:
        reference: Earlier timestamp used as the subtraction baseline.
        now: Current timestamp to compare against.

    Returns:
        Elapsed duration with timezone-awareness normalized as needed.
    """
    comparable_now = normalize_now_for_reference(now, reference)
    return comparable_now - reference


def is_timeout_exceeded(reference: datetime, timeout_minutes: int, *, now: datetime) -> bool:
    """Return whether a lock timeout has been exceeded.

    Args:
        reference: Lock acquisition timestamp.
        timeout_minutes: Timeout in minutes. Non-positive means no timeout.
        now: Current timestamp used for comparison.

    Returns:
        ``True`` when elapsed time is greater than the configured timeout.
    """
    if timeout_minutes <= 0:
        return False
    return elapsed_since(reference, now=now) > timedelta(minutes=timeout_minutes)
