#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM model for Arena per-user signup reputation snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Mapped, relationship

from arena.database import ArenaBase
from shared.db_schema.arena import arena_user_reputation as arena_user_reputation_table

if TYPE_CHECKING:
    from arena.models.arena_users import ArenaUser


class ArenaUserReputation(ArenaBase):
    """A per-user IPQualityScore reputation snapshot captured at signup.

    Attributes:
        id: UUID string primary key.
        user_id: FK to arena_users (unique; one snapshot per user).
        signup_ip: Client IP captured at signup, or None when backfilled.
        ip_fraud_score: IPQualityScore fraud score (0-100) for the signup IP.
        ip_report: Full IPReputation dict, or None when unavailable.
        ip_checked_at: Timestamp of the IP reputation lookup.
        email_fraud_score: IPQualityScore email fraud score (0-100).
        email_overall_score: IPQualityScore email validity confidence (0-4).
        email_report: Full EmailReputation dict, or None when unavailable.
        email_checked_at: Timestamp of the email reputation lookup.
        created_at: Record creation timestamp.
        updated_at: Record last-update timestamp.
        user: Back-reference to the owning ArenaUser.
    """

    __table__ = arena_user_reputation_table

    id: Mapped[str]
    user_id: Mapped[str]
    signup_ip: Mapped[str | None]
    ip_fraud_score: Mapped[int | None]
    ip_report: Mapped[dict[str, Any] | None]
    ip_checked_at: Mapped[datetime | None]
    email_fraud_score: Mapped[int | None]
    email_overall_score: Mapped[int | None]
    email_report: Mapped[dict[str, Any] | None]
    email_checked_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    user: Mapped[ArenaUser] = relationship(
        "ArenaUser",
        back_populates="reputation",
        foreign_keys=[arena_user_reputation_table.c.user_id],
    )
