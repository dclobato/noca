#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add the forward index used to resolve Arena authentication lockouts.

Authentication throttle keys contain HMAC-SHA256 account identifiers. Those
hashes cannot be reversed, and resolving them by recomputing every candidate
for every Arena user makes an administrative page scan the entire user table.
These tables materialize the forward mapping so the page queries only hashes
that are currently locked.

Which secret derived a hash is part of its identity, because rotating
``JWT_SECRET_KEY`` invalidates every hash at once. That generation is stored
once per secret in ``arena_throttle_secret_versions`` and referenced from each
mapping row by a 4-byte integer rather than repeating a 64-character
fingerprint on every row: the fingerprint is the leading column of a composite
primary key, so carrying it inline made the index larger than the table it
indexes. Dropping a generation row cascades to its mappings, which is how a
rotation retires the old hashes.

A composite primary key keeps the mapping many-to-many because two users can
legitimately share a candidate identifier through email canonicalization.

This is a low-churn lookup table. Rows change when a user is created or
deleted, when a login identity changes, and in one bounded rebuild after this
migration backfills or after the JWT secret rotates -- Arena's startup skips
the rebuild entirely once the current generation covers every user. It does not
need custom per-table autovacuum settings.

Revision ID: 202609080001
Revises: 202609070001
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202609080001"
down_revision: str | Sequence[str] | None = "202609070001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the throttle-secret generation table and the hash lookup table."""
    op.create_table(
        "arena_throttle_secret_versions",
        sa.Column(
            "id",
            sa.Integer(),
            sa.Identity(),
            autoincrement=True,
            nullable=False,
            comment="Small surrogate key referenced by every throttle-hash row",
        ),
        sa.Column(
            "secret_fingerprint",
            sa.String(length=64),
            nullable=False,
            comment="SHA-256 fingerprint of the JWT secret that derived this generation of hashes",
        ),
        sa.Column(
            "dta_criacao",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="When this secret generation was first indexed",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_arena_throttle_secret_versions"),
        sa.UniqueConstraint("secret_fingerprint", name="uq_arena_throttle_secret_versions_fingerprint"),
    )
    op.create_table(
        "arena_user_throttle_hashes",
        sa.Column(
            "secret_version_id",
            sa.Integer(),
            nullable=False,
            comment="Secret generation that derived this hash; dropping the generation drops its rows",
        ),
        sa.Column(
            "identifier_hash",
            sa.String(length=64),
            nullable=False,
            comment="HMAC-SHA256 account identifier used in Arena authentication throttle keys.",
        ),
        sa.Column(
            "arena_user_id",
            sa.String(length=36),
            nullable=False,
            comment="Registered Arena user whose identifier recipe produced this hash.",
        ),
        sa.ForeignKeyConstraint(
            ["secret_version_id"],
            ["arena_throttle_secret_versions.id"],
            name="fk_arena_user_throttle_hashes_version",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["arena_user_id"],
            ["arena_users.id"],
            name="fk_arena_user_throttle_hashes_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "secret_version_id",
            "identifier_hash",
            "arena_user_id",
            name="pk_arena_user_throttle_hashes",
        ),
    )
    op.create_index(
        "ix_arena_user_throttle_hashes_user_version",
        "arena_user_throttle_hashes",
        ["arena_user_id", "secret_version_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the throttle-hash lookup table and its generation table."""
    op.drop_index(
        "ix_arena_user_throttle_hashes_user_version",
        table_name="arena_user_throttle_hashes",
    )
    op.drop_table("arena_user_throttle_hashes")
    op.drop_table("arena_throttle_secret_versions")
