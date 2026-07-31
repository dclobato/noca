#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Backfill human-readable actor labels for admin-action events.

Revision ID: 202607220001
Revises: 202607200001
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607220001"
down_revision: str | Sequence[str] | None = "202607200001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Backfill missing admin-action labels from each module's login table."""
    op.execute(
        sa.text(
            """
            UPDATE security_events
            SET actor_label = (
                SELECT users.username
                FROM users
                WHERE users.id = security_events.actor_user_id
            )
            WHERE module = 'web'
              AND event_type = 'admin_action'
              AND actor_label IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM users
                  WHERE users.id = security_events.actor_user_id
              )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE security_events
            SET actor_label = (
                SELECT uber_admins.username
                FROM uber_admins
                WHERE uber_admins.id = security_events.actor_user_id
            )
            WHERE module = 'web'
              AND event_type = 'admin_action'
              AND actor_label IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM uber_admins
                  WHERE uber_admins.id = security_events.actor_user_id
              )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE security_events
            SET actor_label = (
                SELECT arena_users.email_normalizado
                FROM arena_users
                WHERE arena_users.id = security_events.actor_user_id
            )
            WHERE module = 'arena'
              AND event_type = 'admin_action'
              AND actor_label IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM arena_users
                  WHERE arena_users.id = security_events.actor_user_id
              )
            """
        )
    )


def downgrade() -> None:
    """Leave backfilled audit labels intact because their origin is indistinguishable."""
