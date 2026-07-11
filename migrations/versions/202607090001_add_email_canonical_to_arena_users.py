#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add email_canonical to arena_users.

Revision ID: 202607090001
Revises: 202607080002
Create Date: 2026-07-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607090001"
down_revision: str | Sequence[str] | None = "202607080002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMENT = (
    "Canonical/root email (plus-tags and local-part dots stripped) "
    "used to detect mailbox-alias duplicate sign-ups; not unique."
)

# Backfill mirrors EmailValidationService.canonicalize on the already-normalised
# email_normalizado value: strip everything from the first '+' in the local part,
# remove all dots from the local part, keep the domain as-is. The CASE guards the
# empty-local fallback (e.g. "+tag@x") by keeping the original local part.
_BACKFILL = sa.text(
    """
    UPDATE arena_users
    SET email_canonical = CASE
        WHEN replace(split_part(split_part(email_normalizado, '@', 1), '+', 1), '.', '') = ''
            THEN split_part(email_normalizado, '@', 1) || '@' || split_part(email_normalizado, '@', 2)
        ELSE replace(split_part(split_part(email_normalizado, '@', 1), '+', 1), '.', '')
            || '@' || split_part(email_normalizado, '@', 2)
    END
    WHERE email_canonical IS NULL
    """
)


def upgrade() -> None:
    """Add the nullable email_canonical column, backfill it, and index it."""
    op.add_column(
        "arena_users",
        sa.Column("email_canonical", sa.String(180), nullable=True, comment=_COMMENT),
    )
    op.execute(_BACKFILL)
    op.create_index(
        "ix_arena_users_email_canonical",
        "arena_users",
        ["email_canonical"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the email_canonical index and column."""
    op.drop_index("ix_arena_users_email_canonical", table_name="arena_users")
    op.drop_column("arena_users", "email_canonical")
