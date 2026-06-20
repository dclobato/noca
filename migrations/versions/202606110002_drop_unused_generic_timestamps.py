#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""drop unused generic created_at/updated_at columns

Revision ID: 202606110002
Revises: 202606110001
Create Date: 2026-06-11 00:00:00.000000

These generic audit columns were never read: membership history is keyed by
``event_date``; registration requests track ``requested_at``/``decided_at``; and
both submission sidecar tables carry their own domain timestamps
(``ai_response_at`` / ``feedback_at``) that already record when the row was
written.

Columns dropped:
  - arena_class_memberships: created_at, updated_at
  - arena_class_registration_requests: created_at, updated_at
  - arena_submission_ai_reviews: created_at
  - arena_submission_teacher_feedback: created_at

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606110002"
down_revision: str | Sequence[str] | None = "202606110001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables that lose both created_at and updated_at.
_PAIR_TABLES = ("arena_class_memberships", "arena_class_registration_requests")
# Tables that lose only created_at (they keep a domain timestamp).
_CREATED_ONLY_TABLES = ("arena_submission_ai_reviews", "arena_submission_teacher_feedback")


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )


def upgrade() -> None:
    """Upgrade schema."""
    for table in _PAIR_TABLES:
        op.drop_column(table, "updated_at")
        op.drop_column(table, "created_at")
    for table in _CREATED_ONLY_TABLES:
        op.drop_column(table, "created_at")


def downgrade() -> None:
    """Downgrade schema."""
    for table in _CREATED_ONLY_TABLES:
        op.add_column(table, _created_at())
    for table in _PAIR_TABLES:
        op.add_column(table, _created_at())
        op.add_column(table, _updated_at())
