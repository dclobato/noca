#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add actor_label to security_events.

Revision ID: 202607080002
Revises: 202607080001
Create Date: 2026-07-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607080002"
down_revision: str | Sequence[str] | None = "202607080001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMENT = "Human-readable login of the actor, snapshotted at event time (email/username)."


def upgrade() -> None:
    """Add the nullable actor_label column to security_events.

    Width is a deliberate audit-label size (320) with headroom over the source
    login columns (email_normalizado is String(180)) so a valid stored login can
    never overflow and fail the audit insert.
    """
    op.add_column(
        "security_events",
        sa.Column("actor_label", sa.String(320), nullable=True, comment=_COMMENT),
    )


def downgrade() -> None:
    """Drop the actor_label column from security_events."""
    op.drop_column("security_events", "actor_label")
