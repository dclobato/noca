#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Precomputed per-user submission heatmap for the Arena profile page.

One row per user holds the latest computed snapshot (no history). The
``data`` column stores a JSON array of ``["YYYY-MM-DD", count]`` pairs
(non-zero days only) assembled by the rating worker
(``shared.services.arena_heatmap``) so the profile page can render the
calendar heatmap without running aggregate queries on every request.
``range_start`` and ``range_end`` record the exact UTC date window used at
computation time so the client can size and align the calendar correctly.
"""

from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String, Table, func

from .._base import _utcnow, metadata

arena_user_submission_heatmap = Table(
    "arena_user_submission_heatmap",
    metadata,
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to arena_users. CASCADE so orphans are removed automatically.",
    ),
    Column(
        "data",
        JSON,
        nullable=False,
        comment="Array of [YYYY-MM-DD, count] pairs for days with at least one submission.",
    ),
    Column(
        "range_start",
        String(10),
        nullable=False,
        comment="YYYY-MM-DD UTC date of the first day in the 52-week window.",
    ),
    Column(
        "range_end",
        String(10),
        nullable=False,
        comment="YYYY-MM-DD UTC date of the last day in the window (the computation date).",
    ),
    Column(
        "computed_at",
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    ),
)
