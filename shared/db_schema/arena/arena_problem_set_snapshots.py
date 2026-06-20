#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definitions for Arena problem-set rating snapshots.

After a problem set's deadline passes, a snapshot freezes, per user who submitted
anything to the set, the sum of the *current* ratings of the problems they got AC
on. Two tables capture this:

  - arena_problem_set_user_snapshots: one row per user who submitted to the set,
    with the summed rating of their AC'd problems (0 when none).
  - arena_problem_set_problem_snapshots: one row per (user, problem) the user got
    AC on, with the problem's rating captured at snapshot time.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table

from .._base import metadata

arena_problem_set_user_snapshots = Table(
    "arena_problem_set_user_snapshots",
    metadata,
    Column(
        "problem_set_id",
        String(36),
        ForeignKey("arena_problem_sets.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to arena_problem_sets.",
    ),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Arena user who submitted anything to the set.",
    ),
    Column(
        "total_rating",
        Integer,
        nullable=False,
        comment="Sum of current ratings of the problems this user got AC on (0 if none).",
    ),
    Column(
        "snapshot_at",
        DateTime(timezone=True),
        nullable=False,
        comment="When the snapshot was taken.",
    ),
)

arena_problem_set_problem_snapshots = Table(
    "arena_problem_set_problem_snapshots",
    metadata,
    Column(
        "problem_set_id",
        String(36),
        ForeignKey("arena_problem_sets.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to arena_problem_sets.",
    ),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        primary_key=True,
        comment="Arena user who got AC on the problem.",
    ),
    Column(
        "problem_id",
        String(36),
        ForeignKey("arena_problems.id", ondelete="CASCADE"),
        primary_key=True,
        comment="FK to arena_problems; a problem in the set the user got AC on.",
    ),
    Column(
        "rating",
        Integer,
        nullable=False,
        comment="The problem's rating captured at snapshot time.",
    ),
)
