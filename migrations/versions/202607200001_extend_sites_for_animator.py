"""Extend sites/contests for the animator and add site_secrets.

Revision ID: 202607200001
Revises: 202607180003
Create Date: 2026-07-20 00:01:00.000000

"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607200001"
down_revision: str | Sequence[str] | None = "202607180003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "contests",
        sa.Column(
            "animator_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Gate for the animator module: only enabled contests expose snapshot/events/reveal.",
        ),
    )
    # Batch mode keeps the CHECK constraint addition portable: SQLite cannot add a
    # table constraint through a bare ALTER TABLE, so it recreates the table instead,
    # while PostgreSQL emits direct ALTER TABLE statements.
    with op.batch_alter_table("sites") as batch_op:
        batch_op.add_column(
            sa.Column(
                "gold_cutoff",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
                comment="Maximum ranking position (inclusive) awarded a gold medal in this site.",
            )
        )
        batch_op.add_column(
            sa.Column(
                "silver_cutoff",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("2"),
                comment="Maximum ranking position (inclusive) awarded a silver medal in this site.",
            )
        )
        batch_op.add_column(
            sa.Column(
                "bronze_cutoff",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("3"),
                comment="Maximum ranking position (inclusive) awarded a bronze medal in this site.",
            )
        )
        batch_op.create_check_constraint(
            "ck_sites_medal_cutoffs_ordered",
            "gold_cutoff >= 1 AND gold_cutoff <= silver_cutoff AND silver_cutoff <= bronze_cutoff",
        )

    op.create_table(
        "site_secrets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "contest_id",
            sa.String(length=36),
            nullable=False,
            comment="Contest this operator secret authorizes. Always required, even for a global secret.",
        ),
        sa.Column(
            "site_id",
            sa.String(length=36),
            nullable=True,
            comment="Site this secret authorizes. NULL means a contest-global control secret.",
        ),
        sa.Column(
            "secret_digest",
            sa.String(length=64),
            nullable=False,
            comment="Fixed-length digest of the operator token. The plaintext is never stored.",
        ),
        sa.Column(
            "label",
            sa.String(length=200),
            nullable=False,
            comment="Human-readable label for the secret, e.g. 'Operador SP' or 'Global'.",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["contest_id"], ["contests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["contest_id", "site_id"],
            ["sites.contest_id", "sites.id"],
            name="fk_site_secrets_contest_site",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("contest_id", "secret_digest", name="uq_site_secrets_contest_secret_digest"),
        sa.CheckConstraint("length(secret_digest) = 64", name="ck_site_secrets_digest_length"),
    )
    op.create_index("ix_site_secrets_contest_id", "site_secrets", ["contest_id"])
    op.create_index("ix_site_secrets_site_id", "site_secrets", ["site_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_site_secrets_site_id", table_name="site_secrets")
    op.drop_index("ix_site_secrets_contest_id", table_name="site_secrets")
    op.drop_table("site_secrets")

    with op.batch_alter_table("sites") as batch_op:
        batch_op.drop_constraint("ck_sites_medal_cutoffs_ordered", type_="check")
        batch_op.drop_column("bronze_cutoff")
        batch_op.drop_column("silver_cutoff")
        batch_op.drop_column("gold_cutoff")

    op.drop_column("contests", "animator_enabled")
