"""Notify teams about judge/admin announcements.

Revision ID: 202608250001
Revises: 202608210001
Create Date: 2026-08-25
"""

#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202608250001"
down_revision: str | Sequence[str] | None = "202608210001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Classify existing rows. The author's current role is the only signal an existing row
#: carries, and it is exactly the rule the running code applied until now.
#:
#: Both statements are deliberately written in portable SQL -- a subquery rather than
#: ``UPDATE ... FROM``, ``CURRENT_TIMESTAMP`` rather than ``now()``, and a bare enum label
#: comparison rather than a ``::text`` cast -- so the test suite can execute the exact
#: text the migration runs instead of a paraphrase of it.
BACKFILL_ANNOUNCEMENT_FLAG = (
    "UPDATE clarifications SET is_announcement = true "
    "WHERE team_id IN (SELECT id FROM users WHERE role <> 'TEAM')"
)

#: Mark every announcement that exists now read by every team that exists now, so the
#: upgrade does not badge the installed base with the whole announcement history. Teams
#: created later are deliberately not covered: they should see what they missed.
BACKFILL_READ_MARKERS = (
    "INSERT INTO clarification_reads (clarification_id, user_id, read_at) "
    "SELECT c.id, t.id, CURRENT_TIMESTAMP FROM clarifications AS c "
    "JOIN users AS a ON a.id = c.team_id "
    "JOIN users AS t ON t.contest_id = a.contest_id AND t.role = 'TEAM' "
    "WHERE c.is_announcement"
)


def upgrade() -> None:
    """Store the announcement flag and per-team read markers, without notifying for history."""
    op.add_column(
        "clarifications",
        sa.Column(
            "is_announcement",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment=(
                "True for a judge/admin announcement, which is one row read by many teams. "
                "Stored rather than derived from the author's role, which is mutable."
            ),
        ),
    )
    op.execute(sa.text(BACKFILL_ANNOUNCEMENT_FLAG))

    op.create_table(
        "clarification_reads",
        sa.Column(
            "clarification_id",
            sa.String(length=36),
            sa.ForeignKey("clarifications.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to the announcement that was read.",
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            comment="FK to the team that read it.",
        ),
        sa.Column(
            "read_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Time when the announcement was first rendered to this team.",
        ),
    )
    op.create_index("ix_clarification_reads_user_id", "clarification_reads", ["user_id"])

    op.execute(sa.text(BACKFILL_READ_MARKERS))


def downgrade() -> None:
    """Drop the per-team read markers and the announcement flag."""
    op.drop_index("ix_clarification_reads_user_id", table_name="clarification_reads")
    op.drop_table("clarification_reads")
    op.drop_column("clarifications", "is_announcement")
