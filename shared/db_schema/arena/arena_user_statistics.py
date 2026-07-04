#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Precomputed per-user statistics for the Arena public profile page.

One row per user holds the latest computed snapshot only (no history). The
``data`` column stores a JSON blob assembled by the rating worker
(``shared.services.arena_stats``) so the public profile page can render the
verdict and language doughnut charts without running heavy aggregate queries
on every request.
"""

from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String, Table, func

from .._base import _utcnow, metadata

arena_user_statistics = Table(
    "arena_user_statistics",
    metadata,
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to arena_users.",
    ),
    Column(
        "data",
        JSON,
        nullable=False,
        comment="Precomputed statistics payload: verdicts, languages.",
    ),
    Column(
        "computed_at",
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    ),
)
