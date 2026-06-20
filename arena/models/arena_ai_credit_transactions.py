#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""ORM model for Arena AI credit transaction history.

Each row records one credit event: a top-up by an admin, a single-credit
consumption triggered by an AI review request, or a refund when a
platform-funded batch review is locally expired without ever completing.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Mapped, relationship

from arena.database import ArenaBase
from shared.db_schema.arena.arena_ai_credit_transactions import (
    arena_ai_credit_transactions as arena_ai_credit_transactions_table,
)

if TYPE_CHECKING:
    from arena.models.arena_submissions import ArenaSubmission
    from arena.models.arena_users import ArenaUser


class ArenaAiCreditTransaction(ArenaBase):
    """One AI credit balance event for an Arena user.

    Attributes:
        id: UUID string primary key.
        user_id: FK to arena_users — the user whose balance changed.
        amount: Credit delta (positive for top-up and refund, -1 for consumption).
        balance_after: Snapshot of ai_backend_credits after this transaction.
        transaction_type: 'topup', 'consumption', or 'refund'.
        submission_id: FK to arena_submissions; set for consumption and refund rows.
        admin_id: FK to arena_users (admin); set only for top-up rows.
        created_at: UTC timestamp of the event.
        user: Relationship to the target ArenaUser.
        submission: Relationship to the ArenaSubmission that triggered consumption.
        admin: Relationship to the ArenaUser admin who performed a top-up.
    """

    __table__ = arena_ai_credit_transactions_table

    id: Mapped[str]
    user_id: Mapped[str]
    amount: Mapped[int]
    balance_after: Mapped[int]
    transaction_type: Mapped[str]
    submission_id: Mapped[str | None]
    admin_id: Mapped[str | None]
    created_at: Mapped[datetime]

    user: Mapped[ArenaUser] = relationship(
        "ArenaUser",
        foreign_keys=[arena_ai_credit_transactions_table.c.user_id],
        lazy="select",
    )
    submission: Mapped[ArenaSubmission | None] = relationship(
        "ArenaSubmission",
        foreign_keys=[arena_ai_credit_transactions_table.c.submission_id],
        lazy="select",
    )
    admin: Mapped[ArenaUser | None] = relationship(
        "ArenaUser",
        foreign_keys=[arena_ai_credit_transactions_table.c.admin_id],
        lazy="select",
    )
