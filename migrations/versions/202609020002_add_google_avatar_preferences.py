"""Add cached Google avatars and effective-avatar cache revisions.

Revision ID: 202609020002
Revises: 202609020001
Create Date: 2026-09-02
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# Google identity rows remain low-churn account metadata. Even when a linked
# user signs in through Google, only one row is touched, and avatar bytes are
# replaced only after a bounded successful download. This does not warrant a
# custom per-table autovacuum policy.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609020002"
down_revision: str | Sequence[str] | None = "202609020001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add Google picture storage, source selection, and avatar revisioning."""
    op.add_column(
        "arena_user_google_identities",
        sa.Column(
            "google_picture_url",
            sa.String(length=2048),
            nullable=True,
            comment="Latest optional OpenID Connect picture claim; never served directly to browsers.",
        ),
    )
    op.add_column(
        "arena_user_google_identities",
        sa.Column(
            "google_avatar_base64",
            sa.Text(),
            nullable=True,
            comment="Validated, resized local cache of the Google profile picture.",
        ),
    )
    op.add_column(
        "arena_user_google_identities",
        sa.Column(
            "google_avatar_mime",
            sa.String(length=129),
            nullable=True,
            comment="Detected MIME type of the cached Google avatar.",
        ),
    )
    op.add_column(
        "arena_user_google_identities",
        sa.Column(
            "google_avatar_refreshed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp of the last successful Google avatar download.",
        ),
    )
    op.add_column(
        "arena_user_google_identities",
        sa.Column(
            "use_google_avatar",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Whether the canonical Arena avatar endpoint serves the cached Google avatar.",
        ),
    )
    op.add_column(
        "arena_users",
        sa.Column(
            "avatar_revision",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Monotonic cache-busting revision for the effective Arena avatar.",
        ),
    )


def downgrade() -> None:
    """Remove Google avatar storage, source selection, and avatar revisioning."""
    op.drop_column("arena_users", "avatar_revision")
    op.drop_column("arena_user_google_identities", "use_google_avatar")
    op.drop_column("arena_user_google_identities", "google_avatar_refreshed_at")
    op.drop_column("arena_user_google_identities", "google_avatar_mime")
    op.drop_column("arena_user_google_identities", "google_avatar_base64")
    op.drop_column("arena_user_google_identities", "google_picture_url")
