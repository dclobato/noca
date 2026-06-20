#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Precomputed per-problem statistics for the Arena statistics page.

One row per problem holds the latest computed snapshot only (no history). The
``data`` column stores a JSON blob assembled by the rating worker
(``shared.services.arena_stats``) so the Arena statistics page can render charts
and tables without running heavy aggregate queries on every request.
"""

from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String, Table, func

from .._base import _utcnow, metadata

arena_problem_statistics = Table(
    "arena_problem_statistics",
    metadata,
    Column(
        "problem_id",
        String(36),
        ForeignKey("arena_problems.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to arena_problems.",
    ),
    Column(
        "data",
        JSON,
        nullable=False,
        comment="Precomputed statistics payload: verdicts, languages, time/memory stats, histogram.",
    ),
    Column(
        "computed_at",
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    ),
)
