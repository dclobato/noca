#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM model for Arena user gamification badges."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

from arena.database import ArenaBase
from shared.db_schema.arena import arena_user_badges as arena_user_badges_table
from shared.enumerations import ArenaBadge

if TYPE_CHECKING:
    from arena.models.arena_submissions import ArenaSubmission
    from arena.models.arena_users import ArenaUser


class ArenaUserBadge(ArenaBase):
    """A single gamification badge earned by an Arena user.

    Attributes:
        id: UUID string primary key.
        user_id: FK to arena_users.
        badge: The earned badge identifier.
        awarded_at: Timestamp when the badge was awarded.
        submission_id: FK to the submission that earned the badge. Never None:
            a badge that cannot name its work is not awarded.
        created_at: Record creation timestamp.
        user: Back-reference to the owning ArenaUser.
        submission: The awarding submission.
    """

    __table__ = arena_user_badges_table

    id: Mapped[str]
    user_id: Mapped[str]
    badge: Mapped[ArenaBadge]
    awarded_at: Mapped[datetime]
    submission_id: Mapped[str]
    created_at: Mapped[datetime]

    user: Mapped[ArenaUser] = relationship(
        "ArenaUser",
        back_populates="badges",
        foreign_keys=[arena_user_badges_table.c.user_id],
    )
    submission: Mapped[ArenaSubmission] = relationship(
        "ArenaSubmission",
        foreign_keys=[arena_user_badges_table.c.submission_id],
        lazy="select",
    )
