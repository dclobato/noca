#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ICPC-style timestamp utilities shared by the web layer and the autojudge worker."""

from __future__ import annotations

from datetime import datetime


def format_compact_duration(total_seconds: int) -> str:
    """Format elapsed seconds using the two most relevant compact units.

    Args:
        total_seconds: Non-negative whole seconds to format.

    Returns:
        Seconds up to one minute, minutes and seconds up to one hour, or hours
        and minutes for longer durations.

    Raises:
        ValueError: If ``total_seconds`` is negative.
    """
    if total_seconds < 0:
        raise ValueError("Total seconds cannot be negative")
    if total_seconds <= 60:
        return f"{total_seconds}s"

    minutes, seconds = divmod(total_seconds, 60)
    if total_seconds <= 3600:
        return f"{minutes}m{seconds:02d}s"

    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def compute_timestamp_seconds(contest_start: datetime, event_time: datetime) -> int:
    """Return the elapsed non-negative second offset from contest start."""
    total_seconds = int((event_time.replace(tzinfo=None) - contest_start.replace(tzinfo=None)).total_seconds())
    return max(0, total_seconds)


def display_minutes_from_seconds(timestamp_seconds: int | None) -> int | None:
    """Return whole display minutes by truncating a second offset."""
    if timestamp_seconds is None:
        return None
    return timestamp_seconds // 60


def compute_timestamp_minutes(contest_start: datetime, event_time: datetime) -> int:
    """Return an ICPC-style minute offset from contest start to *event_time*.

    The offset is **truncated** to whole minutes, which is the ICPC rule: a
    solve at 60 min 45 s scores 60 penalty minutes, not 61.

    Args:
        contest_start: Contest start datetime. Timezone info must match *event_time*.
        event_time: The moment being converted to a contest-relative minute offset.

    Returns:
        Non-negative integer minute offset.
    """
    return compute_timestamp_seconds(contest_start, event_time) // 60


def icpc_minutes_from_seconds(timestamp_seconds: int | None) -> int | None:
    """Convert a second offset to the ICPC minute representation.

    The offset is **truncated**, never rounded: a solve at 60 min 45 s scores 60
    penalty minutes. This is the scoring path, so the distinction is not
    cosmetic -- rounding shifted the penalty of roughly half of all solves by a
    minute and could reorder standings.

    Args:
        timestamp_seconds: Contest-relative second offset, or ``None``.

    Returns:
        Whole minutes, or ``None`` when the offset is ``None``.
    """
    if timestamp_seconds is None:
        return None
    return max(0, timestamp_seconds) // 60
