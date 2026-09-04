"""Add the shared announcement board table.

Revision ID: 202609010004
Revises: 202609010003
Create Date: 2026-09-01
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609010004"
down_revision: str | Sequence[str] | None = "202609010003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create ``announcements``: one table for both domains, publisher snapshotted, no FK."""
    op.create_table(
        "announcements",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "domain",
            sa.String(length=16),
            nullable=False,
            comment="Publishing surface: 'web' or 'arena'. Each surface lists only its own domain.",
        ),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, comment="Markdown source (LaTeX and Mermaid allowed)."),
        sa.Column(
            "required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Arena only: users must acknowledge the announcement before continuing.",
        ),
        sa.Column(
            "published_by_id",
            sa.String(length=36),
            nullable=False,
            comment="Opaque id of the publisher in the identity table named by domain; no FK on purpose.",
        ),
        sa.Column(
            "published_by_label",
            sa.String(length=320),
            nullable=False,
            comment="Login/username of the publisher, snapshotted at publish time.",
        ),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("domain IN ('web', 'arena')", name="ck_announcements_domain"),
    )
    op.create_index(
        "ix_announcements_domain_published_at",
        "announcements",
        ["domain", sa.text("published_at DESC")],
    )


def downgrade() -> None:
    """Drop the announcement board table."""
    op.drop_index("ix_announcements_domain_published_at", table_name="announcements")
    op.drop_table("announcements")
