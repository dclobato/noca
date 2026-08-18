#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

"""Add Arena problems' editorial release policy.

Arena-only: the Web ``problems`` table has no equivalent column. The value
records when an editorial should become visible to participants (never,
always, or after an Accepted verdict); nothing yet reads it to gate
visibility. A server default backfills existing rows to ``never`` in the same
statement, so no separate backfill pass is needed.

Revision ID: 202608180001
Revises: 202608170001
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202608180001"
down_revision: str | None = "202608170001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY_VALUES = ("never", "always", "after_ac")


def _policy_enum(*, create_type: bool = True) -> postgresql.ENUM:
    """Return the editorial-release-policy PG enum type descriptor."""
    return postgresql.ENUM(*_POLICY_VALUES, name="arenaeditorialreleasepolicy", create_type=create_type)


def upgrade() -> None:
    """Add editorial_release_policy, NOT NULL, defaulting existing and new rows to 'never'."""
    bind = op.get_bind()
    _policy_enum().create(bind, checkfirst=True)
    op.add_column(
        "arena_problems",
        sa.Column(
            "editorial_release_policy",
            _policy_enum(create_type=False),
            nullable=False,
            server_default="never",
            comment="When the editorial is released to participants. Not yet enforced anywhere.",
        ),
    )


def downgrade() -> None:
    """Drop the column, then the enum type."""
    op.drop_column("arena_problems", "editorial_release_policy")
    _policy_enum().drop(op.get_bind(), checkfirst=True)
