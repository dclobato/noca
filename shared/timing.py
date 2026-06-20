#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
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

    Rules:
    - Events within the first 60 seconds → 0
    - Otherwise: remainder ≤ 30 s → floor; > 30 s → ceiling (round to nearest minute)

    Args:
        contest_start: Contest start datetime. Timezone info must match *event_time*.
        event_time: The moment being converted to a contest-relative minute offset.

    Returns:
        Non-negative integer minute offset.
    """
    total_seconds = compute_timestamp_seconds(contest_start, event_time)
    if total_seconds < 60:
        return max(0, total_seconds // 60)
    minutes, remainder = divmod(total_seconds, 60)
    return minutes if remainder <= 30 else minutes + 1


def icpc_minutes_from_seconds(timestamp_seconds: int | None) -> int | None:
    """Convert a second offset to the ICPC minute representation."""
    if timestamp_seconds is None:
        return None
    if timestamp_seconds < 60:
        return max(0, timestamp_seconds // 60)
    minutes, remainder = divmod(timestamp_seconds, 60)
    return minutes if remainder <= 30 else minutes + 1
