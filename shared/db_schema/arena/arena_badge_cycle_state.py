#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Singleton state row for the rating worker's badge-assignment loop.

Holds the incremental-pass watermark (``last_processed_at``) and the timestamp of
the last full reconciliation (``last_reconciled_at``). A ``CHECK`` constraint pins
the table to a single row keyed by the fixed id ``"singleton"`` so the rating
worker can read-modify-write it under ``SELECT ... FOR UPDATE`` to serialize cycles.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, String, Table

from .._base import _updated_at_column, metadata

#: The only id this table ever holds; see the CHECK constraint below.
BADGE_CYCLE_STATE_ID = "singleton"

arena_badge_cycle_state = Table(
    "arena_badge_cycle_state",
    metadata,
    Column(
        "id",
        String(16),
        primary_key=True,
        comment="Fixed singleton key; always 'singleton'.",
    ),
    Column(
        "last_processed_at",
        DateTime(timezone=True),
        nullable=True,
        comment="High-watermark: max judgment finished_at processed by the incremental pass.",
    ),
    Column(
        "last_reconciled_at",
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of the last full reconciliation pass over all AC history.",
    ),
    _updated_at_column(),
    CheckConstraint("id = 'singleton'", name="ck_arena_badge_cycle_state_singleton"),
)
