#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Literal

from tabulate import tabulate

from shared.enumerations import VERDICT_BADGE_CLASSES, Verdict
from shared.timing import display_minutes_from_seconds

type ColumnAlignment = Literal["l", "c", "r"]

_SLUGFY_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def format_seconds_compact(total_seconds: int) -> str:
    """
    Converts seconds to 'Xh Ymin Zs', omitting units that are zero.
    Returns '0s' if the input is 0.
    """
    if total_seconds < 0:
        raise ValueError("Total seconds cannot be negative")

    # Calculate hours, minutes, and seconds
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    # Build a list of the parts that are non-zero
    parts = []

    if hours > 0:
        parts.append(f"{hours}h")

    if minutes > 0:
        if hours > 0:
            parts.append(f"{minutes:02d}min")  # Pad minutes with leading zero if hours are present
        else:
            parts.append(f"{minutes}min")

    # We add seconds if they exist, OR if the total time is 0 (to return "0s")
    if seconds > 0 or total_seconds == 0:
        if (hours > 0 or minutes > 0) and seconds < 10:
            parts.append(f"{seconds:02d}s")  # Pad seconds with leading zero if hours or minutes are present
        else:
            parts.append(f"{seconds}s")

    # Join the parts together into a single string
    return " ".join(parts)


def minutes_from_contest_start(contest_start: datetime, timestamp: datetime) -> int:
    """
    Return whole elapsed minutes between contest start and a timestamp.
    """
    return int((timestamp - contest_start).total_seconds() // 60)


def contest_minutes(timestamp_seconds: int | None) -> int | None:
    """Return the display minute value for a contest-relative second offset."""
    return display_minutes_from_seconds(timestamp_seconds)


def format_site_identity(site_name: str | None, base_name: str) -> str:
    """Return a display name optionally prefixed by the user's site.

    Args:
        site_name: The site's display name, if one is associated.
        base_name: The existing display label for the user/team.

    Returns:
        The site-prefixed label in the format ``[site] Name`` when a site is
        present; otherwise the original base name.
    """
    cleaned_site = (site_name or "").strip()
    return f"[{cleaned_site}] {base_name}" if cleaned_site else base_name


def contest_verdict_badge_class(verdict: str, contest: object) -> str:
    """Return the Bootstrap badge class for a verdict in a contest context.

    Args:
        verdict: Verdict enum value.
        contest: Contest-like object with ``accept_pe`` and ``ce_adds_penalty``.

    Returns:
        str: Bootstrap badge classes for the verdict.
    """
    if verdict == Verdict.PE.value and bool(getattr(contest, "accept_pe", False)):
        return "bg-success"
    if verdict == Verdict.CE.value and bool(getattr(contest, "ce_adds_penalty", False)):
        return "bg-warning text-dark"
    return VERDICT_BADGE_CLASSES.get(verdict, "bg-secondary")


def render_prettytable(
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
    *,
    header_alignments: Mapping[str, ColumnAlignment] | None = None,
    max_widths: Mapping[str, int] | None = None,
) -> str:
    """Render an ASCII table using tabulate with grid formatting.

    Args:
        headers: Column header names in display order.
        rows: Data rows, each with the same number of cells as ``headers``.
        header_alignments: Optional alignment per header name ("l", "c", "r").
        max_widths: Optional maximum width per header for wrapped columns.

    Returns:
        Formatted ASCII table string without a trailing newline.
    """
    _align_map: dict[str, str] = {"l": "left", "c": "center", "r": "right"}
    alignments = header_alignments or {}
    colalign = tuple(_align_map.get(alignments.get(h, "l"), "left") for h in headers)
    maxcolwidths = [max_widths.get(h) if max_widths else None for h in headers]
    return str(
        tabulate(list(rows), headers=list(headers), tablefmt="grid", colalign=colalign, maxcolwidths=maxcolwidths)
    )


def slugfy(value: str, *, fallback: str = "") -> str:
    """Return a filesystem-safe slug-like string by replacing invalid runs with underscores."""
    return _SLUGFY_RE.sub("_", value.strip() or fallback)
