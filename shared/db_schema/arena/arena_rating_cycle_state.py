#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Singleton snapshot row for the rating worker's problem-difficulty cycle.

Holds the catalogue-wide difficulty histogram (``data``) and the timestamp of the
cycle that produced it (``computed_at``), written at the end of every
``rate_all_problems()`` run. A ``CHECK`` constraint pins the table to a single row
keyed by the fixed id ``"singleton"`` so the rating worker upserts it in place and
the Arena help page reads exactly one snapshot.
"""

from __future__ import annotations

from sqlalchemy import JSON, CheckConstraint, Column, DateTime, String, Table

from .._base import _updated_at_column, metadata

#: The only id this table ever holds; see the CHECK constraint below.
RATING_CYCLE_STATE_ID = "singleton"

arena_rating_cycle_state = Table(
    "arena_rating_cycle_state",
    metadata,
    Column(
        "id",
        String(16),
        primary_key=True,
        comment="Fixed singleton key; always 'singleton'.",
    ),
    Column(
        "data",
        JSON,
        nullable=True,
        comment="Latest problem-difficulty histogram snapshot payload (JSON).",
    ),
    Column(
        "computed_at",
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of the difficulty cycle that produced the snapshot.",
    ),
    _updated_at_column(),
    CheckConstraint("id = 'singleton'", name="ck_arena_rating_cycle_state_singleton"),
)
