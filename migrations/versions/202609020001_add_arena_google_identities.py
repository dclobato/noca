"""Add Arena Google identities, the placeholder-password flag, and the wider login mode comment.

Revision ID: 202609020001
Revises: 202609010005
Create Date: 2026-09-02
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# `password_is_placeholder` backfills to false for every existing row, which is
# correct rather than merely convenient: every account that exists before this
# revision was created through a signup or admin path that wrote a real password.
# Only a Google-first signup can set it true, and no such signup can have run yet.
#
# No separate index on user_id: the UNIQUE constraint already creates a B-tree
# index over exactly that column, so a second one would only cost writes. This
# matches arena_user_reputation, the 1:1 satellite this table is modelled on.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609020001"
down_revision: str | Sequence[str] | None = "202609010005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_MODE_COMMENT = "Authentication method used: password, 2fa, backup_code"
_NEW_MODE_COMMENT = (
    "Authentication method used: password, 2fa, backup_code, google, google_2fa, google_backup_code"
)


def upgrade() -> None:
    """Create the Google identity table and the two arena_users/login-history changes."""
    op.create_table(
        "arena_user_google_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "user_id",
            sa.String(length=36),
            nullable=False,
            comment=(
                "1:1 FK to arena_users. UNIQUE enforces at most one Google identity per account."
            ),
        ),
        sa.Column(
            "google_sub",
            sa.String(length=255),
            nullable=False,
            comment=(
                "Google's stable 'sub' claim. This, not the email, is the join key: a Google "
                "account's email can change while its subject identifier cannot. UNIQUE enforces "
                "that one Google identity belongs to at most one Arena account."
            ),
        ),
        sa.Column(
            "google_email",
            sa.String(length=180),
            nullable=True,
            comment=(
                "Google account email at link time. Informational only -- shown in the UI so the "
                "user can see which Google account is linked. Never used for lookup."
            ),
        ),
        sa.Column(
            "google_email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Whether Google asserted email_verified for google_email at link time.",
        ),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Timestamp at which this Google identity was linked to the Arena account.",
        ),
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp of the most recent Google login; NULL until the identity is used.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["arena_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_arena_user_google_identities_user_id"),
        sa.UniqueConstraint("google_sub", name="uq_arena_user_google_identities_google_sub"),
    )
    op.add_column(
        "arena_users",
        sa.Column(
            "password_is_placeholder",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment=(
                "True when password_hash is an unusable random placeholder written by a "
                "Google-first signup, so password login can never match it. The "
                "ArenaUser.password setter clears this flag, so setting a real password "
                "self-corrects it."
            ),
        ),
    )

    op.alter_column(
        "arena_login_history",
        "mode",
        existing_type=sa.String(32),
        existing_nullable=True,
        existing_comment=_OLD_MODE_COMMENT,
        comment=_NEW_MODE_COMMENT,
    )


def downgrade() -> None:
    """Drop the Google identity table, the placeholder flag, and restore the mode comment."""
    op.alter_column(
        "arena_login_history",
        "mode",
        existing_type=sa.String(32),
        existing_nullable=True,
        existing_comment=_NEW_MODE_COMMENT,
        comment=_OLD_MODE_COMMENT,
    )
    op.drop_column("arena_users", "password_is_placeholder")
    op.drop_table("arena_user_google_identities")
