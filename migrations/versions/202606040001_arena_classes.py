#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
"""Arena classes, memberships, registration requests, problem set placeholder

Revision ID: 202606040001
Revises: 202606020003
Create Date: 2026-06-04 00:01:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "202606040001"
down_revision: str | Sequence[str] | None = "202606020003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MEMBERSHIP_STATUS = sa.Enum(
    "ACTIVE",
    "REMOVED",
    name="arenaclassmembershipstatus",
)
_REGISTRATION_STATUS = sa.Enum(
    "PENDING",
    "APPROVED",
    "DENIED",
    name="arenaclassregistrationstatus",
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "arena_classes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, comment="Display name of the class"),
        sa.Column("description", sa.Text(), nullable=True, comment="Optional free-text description"),
        sa.Column(
            "teacher_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="RESTRICT"),
            nullable=False,
            comment="Assigned teacher (owner); must be an ARENA_JUDGE user",
        ),
        sa.Column("starts_on", sa.Date(), nullable=False, comment="Date the class starts (date only)"),
        sa.Column("finishes_on", sa.Date(), nullable=False, comment="Date the class finishes (date only)"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("finishes_on >= starts_on", name="ck_arena_classes_dates"),
    )
    op.create_index("ix_arena_classes_teacher_id", "arena_classes", ["teacher_id"])

    op.create_table(
        "arena_class_memberships",
        sa.Column(
            "class_id",
            sa.String(36),
            sa.ForeignKey("arena_classes.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to arena_classes.",
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to arena_users.",
        ),
        sa.Column(
            "event_date",
            sa.Date(),
            primary_key=True,
            comment="Date the status took effect (date only); same-day flips overwrite this row",
        ),
        sa.Column(
            "status",
            _MEMBERSHIP_STATUS,
            nullable=False,
            comment="Membership status effective on event_date: ACTIVE or REMOVED",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_arena_class_memberships_user", "arena_class_memberships", ["user_id"])

    op.create_table(
        "arena_class_registration_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "class_id",
            sa.String(36),
            sa.ForeignKey("arena_classes.id", ondelete="CASCADE"),
            nullable=False,
            comment="FK to arena_classes.",
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="CASCADE"),
            nullable=False,
            comment="Arena user requesting registration.",
        ),
        sa.Column(
            "status",
            _REGISTRATION_STATUS,
            nullable=False,
            server_default="PENDING",
            comment="Request lifecycle: PENDING, APPROVED, or DENIED",
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="When the user submitted the request",
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When a teacher/admin approved or denied the request",
        ),
        sa.Column(
            "decided_by_id",
            sa.String(36),
            sa.ForeignKey("arena_users.id", ondelete="SET NULL"),
            nullable=True,
            comment="User who decided the request (teacher or admin)",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_arena_class_registration_requests_class_id",
        "arena_class_registration_requests",
        ["class_id"],
    )
    op.create_index(
        "uq_arena_class_reg_pending",
        "arena_class_registration_requests",
        ["class_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
        sqlite_where=sa.text("status = 'PENDING'"),
    )

    op.create_table(
        "arena_problem_sets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "class_id",
            sa.String(36),
            sa.ForeignKey("arena_classes.id", ondelete="CASCADE"),
            nullable=False,
            comment="FK to arena_classes; problem sets belong to exactly one class.",
        ),
        sa.Column("name", sa.String(200), nullable=False, comment="Display name of the problem set"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_arena_problem_sets_class_id", "arena_problem_sets", ["class_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("arena_problem_sets")
    op.drop_index("uq_arena_class_reg_pending", table_name="arena_class_registration_requests")
    op.drop_index(
        "ix_arena_class_registration_requests_class_id",
        table_name="arena_class_registration_requests",
    )
    op.drop_table("arena_class_registration_requests")
    op.drop_index("ix_arena_class_memberships_user", table_name="arena_class_memberships")
    op.drop_table("arena_class_memberships")
    op.drop_index("ix_arena_classes_teacher_id", table_name="arena_classes")
    op.drop_table("arena_classes")
    _MEMBERSHIP_STATUS.drop(op.get_bind(), checkfirst=True)
    _REGISTRATION_STATUS.drop(op.get_bind(), checkfirst=True)
