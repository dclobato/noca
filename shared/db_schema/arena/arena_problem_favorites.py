#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definition for Arena user problem favorites."""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, String, Table

from .._base import metadata

arena_problem_favorites = Table(
    "arena_problem_favorites",
    metadata,
    Column(
        "problem_id",
        String(36),
        ForeignKey("arena_problems.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to arena_problems.",
    ),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to arena_users.",
    ),
    Index("ix_arena_problem_favorites_user_id", "user_id"),
)
