#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Arena worker pause control: authoritative pause state and command audit

Revision ID: 202606110003
Revises: 202606110002
Create Date: 2026-06-11 00:03:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606110003"
down_revision: str | Sequence[str] | None = "202606110002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "arena_worker_pause_state",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "worker_class",
            sa.String(32),
            nullable=False,
            comment="WorkerClass value (autojudge or aiassistant).",
        ),
        sa.Column(
            "worker_id",
            sa.String(255),
            nullable=False,
            comment="Stable worker identifier within the class.",
        ),
        sa.Column(
            "paused",
            sa.Boolean,
            nullable=False,
            server_default="false",
            comment="Authoritative paused state for this worker.",
        ),
        sa.Column(
            "paused_by",
            sa.String(320),
            nullable=True,
            comment="Email of the admin who last changed the paused state.",
        ),
        sa.Column(
            "generation",
            sa.BigInteger,
            nullable=False,
            server_default="0",
            comment="Monotonic counter; defeats command replay across resume/restart.",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "worker_class",
            "worker_id",
            name="uq_arena_worker_pause_state_class_id",
        ),
        sa.CheckConstraint(
            "generation >= 0",
            name="ck_arena_worker_pause_state_generation_nonnegative",
        ),
    )

    op.create_table(
        "arena_worker_command_audit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "actor_user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="SET NULL"),
            nullable=True,
            comment="Arena admin who issued the command, when known.",
        ),
        sa.Column("actor_email", sa.String(320), nullable=True),
        sa.Column(
            "action",
            sa.String(16),
            nullable=False,
            comment="Requested action: pause or resume.",
        ),
        sa.Column("worker_class", sa.String(32), nullable=False),
        sa.Column("worker_id", sa.String(255), nullable=False),
        sa.Column(
            "generation",
            sa.BigInteger,
            nullable=True,
            comment="Committed pause-state generation; null when the request was rejected.",
        ),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Moment the command was issued by the route.",
        ),
        sa.Column(
            "outcome",
            sa.String(32),
            nullable=False,
            comment="committed | rejected_disabled | rejected_bad_request.",
        ),
        sa.Column(
            "transport_status",
            sa.String(32),
            nullable=False,
            comment="pending | delivered | transport_failed | n_a.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("arena_worker_command_audit")
    op.drop_table("arena_worker_pause_state")
