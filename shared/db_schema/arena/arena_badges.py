#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Core table definition for Arena user gamification badges."""

from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, String, Table, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum

from shared.enumerations import ArenaBadge

from .._base import _created_at_column, _id_column, _utcnow, metadata

arena_user_badges = Table(
    "arena_user_badges",
    metadata,
    _id_column(),
    Column(
        "user_id",
        String(36),
        ForeignKey("arena_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="FK to arena_users. The user who earned this badge.",
    ),
    Column(
        "badge",
        SAEnum(ArenaBadge, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        comment="Badge identifier; equals the basename of the badge image asset.",
    ),
    Column(
        "awarded_at",
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
        comment="Timestamp when the badge was awarded to the user.",
    ),
    _created_at_column(),
    UniqueConstraint("user_id", "badge", name="uq_arena_user_badges_user_badge"),
)
